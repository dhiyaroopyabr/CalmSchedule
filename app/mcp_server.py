import re
import sys
from mcp.server.fastmcp import FastMCP

# Initialize the FastMCP server
mcp = FastMCP("CalmSchedule-Server")

# In-memory database of calendar events for simulation
CALENDAR_EVENTS = [
    {"id": 1, "title": "Team Sync", "start": "09:00", "end": "10:00"},
    {"id": 2, "title": "Deep Work Block", "start": "11:00", "end": "12:00"},
    {"id": 3, "title": "Project Planning", "start": "14:00", "end": "15:00"},
]

ROUTINE_HABITS = [
    "Insert a 15-minute mindful break between consecutive meetings.",
    "Limit meetings to 45 minutes to allow for prep and physical recovery.",
    "Schedule 90-minute focus blocks with notifications silenced.",
]

@mcp.tool()
def get_calendar_events() -> str:
    """Retrieve the list of calendar events for today."""
    if not CALENDAR_EVENTS:
        return "Your calendar is empty for today."
    
    events_str = "Today's Schedule:\n"
    for e in CALENDAR_EVENTS:
        events_str += f"- [{e['start']} - {e['end']}] {e['title']}\n"
    return events_str

@mcp.tool()
def add_calendar_event(title: str, start_time: str, end_time: str) -> str:
    """Add a new event to the calendar.
    
    Args:
        title: The name of the event or meeting.
        start_time: Start time in format HH:MM (e.g. '13:00').
        end_time: End time in format HH:MM (e.g. '13:45').
    """
    if not re.match(r'^\d{2}:\d{2}$', start_time) or not re.match(r'^\d{2}:\d{2}$', end_time):
        return "Error: Start and end times must be in HH:MM format (e.g., '13:00')."
        
    new_event = {
        "id": len(CALENDAR_EVENTS) + 1,
        "title": title,
        "start": start_time,
        "end": end_time
    }
    CALENDAR_EVENTS.append(new_event)
    print(f"Scheduled: {title} ({start_time}-{end_time})", file=sys.stderr)
    return f"Successfully scheduled event: '{title}' from {start_time} to {end_time}."

@mcp.tool()
def optimize_routine_habits() -> str:
    """Analyze daily habits and recommend structured breaks or scheduling optimizations."""
    habits_str = "Routine Recommendations:\n"
    for idx, habit in enumerate(ROUTINE_HABITS, 1):
        habits_str += f"{idx}. {habit}\n"
    return habits_str

@mcp.tool()
def suggest_mindful_breaks(work_duration_minutes: int) -> str:
    """Suggest mindfulness or movement exercises based on completed work duration.
    
    Args:
        work_duration_minutes: How many minutes of work were completed (e.g., 60, 90).
    """
    if work_duration_minutes < 45:
        return "Light stretch: Roll your shoulders, look away from the screen for 20 seconds, and drink a glass of water."
    elif work_duration_minutes < 90:
        return "Movement break: Take a 5-minute walk, stretch your hamstrings and back, and practice 4-7-8 deep breathing."
    else:
        return "Deep refresh: Take a 15-minute break. Walk outside, do a brief guided meditation, and completely disconnect from screens."

if __name__ == "__main__":
    mcp.run()
