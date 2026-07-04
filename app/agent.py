# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Any
import datetime
import re
import os
import sys
from pydantic import BaseModel, Field
from google.adk.workflow import Workflow, START, node
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App
from google.genai import types

from mcp import StdioServerParameters
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams

from app.config import config

# =====================================================================
# State & Output Schemas
# =====================================================================

class CalmScheduleState(BaseModel):
    user_query: str = ""
    security_passed: bool = True
    security_notes: str = ""
    orchestrator_response: str = ""
    scheduler_response: str = ""
    routine_response: str = ""
    needs_approval: bool = False
    proposed_update: str = ""
    approval_granted: bool = False
    final_result: str = ""
    orchestrator_output: dict = Field(default_factory=dict)

class OrchestratorOutput(BaseModel):
    response: str = Field(description="The response/plan to present to the user.")
    needs_approval: bool = Field(description="True if calendar or routine updates were proposed and require user confirmation.")
    proposed_update: str = Field(description="Details of the proposed update/action, if any.")

# =====================================================================
# MCP Server Configuration & Connection (Phase 3)
# =====================================================================

current_dir = os.path.dirname(os.path.abspath(__file__))
mcp_server_path = os.path.join(current_dir, "mcp_server.py")

mcp_tools = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[mcp_server_path],
        )
    )
)

# =====================================================================
# Sub-agents
# =====================================================================

scheduler_agent = LlmAgent(
    name="scheduler_agent",
    model=config.model,
    instruction="""You are a calendar scheduling specialist for CalmSchedule.
Your job is to manage calendar events, check conflicts, and schedule breaks.
Help the orchestrator by planning optimal time slots.
Always use the tools available to check or update the calendar.""",
    description="Manages calendar events, checks conflicts, and schedules breaks.",
    tools=[mcp_tools],
)

routine_optimizer = LlmAgent(
    name="routine_optimizer",
    model=config.model,
    instruction="""You are a routine optimization specialist for CalmSchedule.
Your job is to analyze daily routines, suggest healthy habits, and suggest break intervals.
Help the orchestrator optimize the user's daily flow.""",
    description="Analyzes routines, suggests healthy habits, and optimizes break schedules.",
    tools=[mcp_tools],
)

# =====================================================================
# Orchestrator Agent (uses specialized agents as tools)
# =====================================================================

orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=config.model,
    instruction="""You are the main CalmSchedule coordinator.
You help the user optimize their daily schedule, manage calendar events, and schedule breaks.
When a request comes in:
1. Delegate scheduling queries to the scheduler_agent.
2. Delegate routine/habit/break optimization queries to the routine_optimizer.
Combine their insights to formulate a response.
If you propose adding/updating calendar events or routine changes, clearly state what updates you propose.
Otherwise, specify that no updates or modifications are needed.""",
    tools=[AgentTool(scheduler_agent), AgentTool(routine_optimizer)],
)

response_formatter = LlmAgent(
    name="response_formatter",
    model=config.model,
    instruction="""You are a response formatting specialist for CalmSchedule.
Your job is to structure the coordinator's final output into the required JSON schema format.
Based on the text input:
- Extract the main response text into the 'response' field.
- Determine if any calendar additions/updates or routine modifications are proposed. If so, set 'needs_approval' to True and write the details in 'proposed_update'.
- Otherwise, set 'needs_approval' to False and 'proposed_update' to an empty string.""",
    output_schema=OrchestratorOutput,
    output_key="orchestrator_output",
)

# =====================================================================
# Workflow Nodes
# =====================================================================

import json

def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    """Checks the query for prompt injection, scrubs PII, and applies domain-specific rules."""
    user_text = ""
    if node_input and node_input.parts:
        user_text = "".join([p.text for p in node_input.parts if p.text])
        
    ctx.state["user_query"] = user_text
    session_id = ctx.session.id if ctx.session else "unknown"
    
    # 1. Prompt Injection Detection
    injection_keywords = [
        "ignore previous instructions", 
        "system prompt", 
        "override rules", 
        "bypass guardrails", 
        "disregard safety"
    ]
    for kw in injection_keywords:
        if kw in user_text.lower():
            # Structured JSON audit log
            audit_log = {
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "severity": "CRITICAL",
                "event": "PROMPT_INJECTION_DETECTED",
                "session_id": session_id,
                "detail": f"Matched keyword: '{kw}'"
            }
            print(json.dumps(audit_log), file=sys.stderr)
            
            ctx.state["security_passed"] = False
            ctx.state["security_notes"] = f"Prompt injection keywords detected: '{kw}'"
            return Event(output="Security Warning: Prompt injection attempt detected.", route="security_incident")
            
    # 2. PII Scrubbing (SSNs, Card Numbers, Emails, Phone Numbers)
    scrubbed = user_text
    
    # SSN
    scrubbed, ssn_count = re.subn(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED SSN]', scrubbed)
    # Credit Card
    scrubbed, cc_count = re.subn(r'\b\d{4}-\d{4}-\d{4}-\d{4}\b', '[REDACTED CARD]', scrubbed)
    # Email
    scrubbed, email_count = re.subn(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[REDACTED EMAIL]', scrubbed)
    # Phone
    scrubbed, phone_count = re.subn(r'\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b', '[REDACTED PHONE]', scrubbed)
    
    if ssn_count > 0 or cc_count > 0 or email_count > 0 or phone_count > 0:
        audit_log = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "severity": "WARNING",
            "event": "PII_SCRUBBED",
            "session_id": session_id,
            "detail": f"Scrubbed: SSN={ssn_count}, CC={cc_count}, Email={email_count}, Phone={phone_count}"
        }
        print(json.dumps(audit_log), file=sys.stderr)
    
    # 3. Domain-Specific Rule: Late night / off-hours scheduling detection or confidential content policy
    has_confidential = "confidential" in scrubbed.lower() or "restricted" in scrubbed.lower()
    
    # Check for off-hours (e.g. 10 PM - 5 AM)
    has_late_night = bool(re.search(r'\b(?:1[0-1]|12|[1-5]):[0-5][0-9]\s*(?:PM|AM|pm|am)\b', scrubbed) or 
                          re.search(r'\b(?:2[2-3]|0[0-5]):[0-5][0-9]\b', scrubbed))
    
    if has_confidential:
        audit_log = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "severity": "WARNING",
            "event": "DOMAIN_POLICY_CHECK",
            "session_id": session_id,
            "detail": "Confidential meeting request detected. Auto-routing to human approval required."
        }
        print(json.dumps(audit_log), file=sys.stderr)
        scrubbed += "\n(Policy Notice: This meeting contains sensitive/confidential topics. Approval is mandatory.)"
        
    if has_late_night:
        audit_log = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "severity": "WARNING",
            "event": "OFF_HOURS_SCHEDULING_DETECTED",
            "session_id": session_id,
            "detail": "Event requested outside standard business hours (08:00 - 18:00)."
        }
        print(json.dumps(audit_log), file=sys.stderr)
        scrubbed += "\n(Policy Notice: Requested slot is outside standard business hours. Approval is mandatory.)"

    # Log successful check
    if ctx.state.get("security_passed", True):
        audit_log = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "severity": "INFO",
            "event": "SECURITY_CHECK_PASSED",
            "session_id": session_id
        }
        print(json.dumps(audit_log), file=sys.stderr)
        
    ctx.state["user_query"] = scrubbed
    return Event(output=scrubbed)

def security_event(ctx: Context, node_input: str) -> Event:
    """Handles security incident routing."""
    ctx.state["security_passed"] = False
    ctx.state["security_notes"] = node_input
    
    audit_log = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "severity": "CRITICAL",
        "event": "SECURITY_INCIDENT_ROUTING",
        "session_id": ctx.session.id if ctx.session else "unknown",
        "detail": "Routing flow to security incident termination."
    }
    print(json.dumps(audit_log), file=sys.stderr)
    return Event(output={"security_incident": True})

def route_after_orchestration(ctx: Context, node_input: dict) -> Event:
    """Inspects the orchestrator's decision and routes accordingly."""
    ctx.state["orchestrator_response"] = node_input.get("response", "")
    needs_approval = node_input.get("needs_approval", False)
    ctx.state["needs_approval"] = needs_approval
    ctx.state["proposed_update"] = node_input.get("proposed_update", "")
    
    if needs_approval:
        return Event(output=node_input, route="needs_approval")
    return Event(output=node_input)

async def human_approval_node(ctx: Context, node_input: Any):
    """Asks for human approval using RequestInput."""
    if not ctx.resume_inputs:
        proposed = ctx.state.get("proposed_update", "schedule update")
        yield RequestInput(
            interrupt_id="approve_update",
            message=f"CalmSchedule proposed the following update:\n{proposed}\n\nDo you approve this change? (yes/no)"
        )
        return
        
    user_response = ctx.resume_inputs.get("approve_update", "").strip().lower()
    if user_response in ["yes", "y", "approve"]:
        ctx.state["approval_granted"] = True
        yield Event(output={"approved": True, "message": "User approved the update."}, state={"approval_granted": True})
    else:
        ctx.state["approval_granted"] = False
        yield Event(output={"approved": False, "message": "User denied the update."}, state={"approval_granted": False})

def final_output(ctx: Context, node_input: dict) -> Event:
    """Prepares final response for UI display."""
    if not ctx.state.get("security_passed", True):
        msg = f"⚠️ Request blocked by CalmSchedule security check:\n{ctx.state.get('security_notes')}"
    elif ctx.state.get("needs_approval", False):
        if ctx.state.get("approval_granted", False):
            msg = f"✅ Update Approved and Applied!\n\nPlan: {ctx.state.get('orchestrator_response')}\nDetails: {ctx.state.get('proposed_update')}"
        else:
            msg = f"❌ Update Denied.\n\nPlan: {ctx.state.get('orchestrator_response')}\n(No changes were applied)"
    else:
        msg = f"✨ Request Completed:\n\n{ctx.state.get('orchestrator_response')}"
        
    # Yield both visual content for UI and output dict
    return Event(
        content=types.Content(
            role='model',
            parts=[types.Part.from_text(text=msg)]
        ),
        output={"result": msg}
    )

# =====================================================================
# Workflow Graph & App Definition
# =====================================================================

root_agent = Workflow(
    name="calm_schedule_workflow",
    state_schema=CalmScheduleState,
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"security_incident": security_event, "__DEFAULT__": orchestrator_agent}),
        (orchestrator_agent, response_formatter),
        (response_formatter, route_after_orchestration),
        (route_after_orchestration, {"needs_approval": human_approval_node, "__DEFAULT__": final_output}),
        (human_approval_node, final_output),
        (security_event, final_output)
    ]
)

app = App(
    root_agent=root_agent,
    name="app",
)
