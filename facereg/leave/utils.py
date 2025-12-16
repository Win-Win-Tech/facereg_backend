"""
Utility functions for leave and weekoff calculations
"""
from datetime import date, timedelta


def get_day_number_of_month(check_date, target_weekday):
    """
    Get which occurrence of a weekday in the month (1st, 2nd, 3rd, 4th, or 5th).
    
    Args:
        check_date: date object (must match target_weekday)
        target_weekday: int, day of week (0=Monday, 1=Tuesday, ..., 6=Sunday)
    
    Returns:
        int: Occurrence number (1-5)
    """
    # Get first day of the month
    first_day = check_date.replace(day=1)
    
    # Calculate days until first occurrence of target weekday
    days_until_first = (target_weekday - first_day.weekday()) % 7
    if days_until_first == 0 and first_day.weekday() == target_weekday:
        # First day is already the target weekday
        first_occurrence = first_day
    else:
        first_occurrence = first_day + timedelta(days=days_until_first)
    
    # Calculate which occurrence of the month this is
    days_diff = (check_date - first_occurrence).days
    occurrence_number = (days_diff // 7) + 1
    
    return occurrence_number


def is_date_weekoff(check_date, patterns):
    """
    Check if a date matches any of the selected weekoff patterns.
    
    Args:
        check_date: date object to check
        patterns: list of pattern strings, e.g., ["SUNDAY", "ODD_SATURDAY", "EVEN_MONDAY"]
    
    Returns:
        bool: True if date is a weekoff, False otherwise
    """
    if not patterns:
        return False
    
    day_of_week = check_date.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
    
    # Map day names to weekday numbers
    day_map = {
        'MONDAY': 0,
        'TUESDAY': 1,
        'WEDNESDAY': 2,
        'THURSDAY': 3,
        'FRIDAY': 4,
        'SATURDAY': 5,
        'SUNDAY': 6,
    }
    
    # Map day names to odd/even pattern names
    odd_patterns = {
        'ODD_MONDAY': 0,
        'ODD_TUESDAY': 1,
        'ODD_WEDNESDAY': 2,
        'ODD_THURSDAY': 3,
        'ODD_FRIDAY': 4,
        'ODD_SATURDAY': 5,
        'ODD_SUNDAY': 6,
    }
    
    even_patterns = {
        'EVEN_MONDAY': 0,
        'EVEN_TUESDAY': 1,
        'EVEN_WEDNESDAY': 2,
        'EVEN_THURSDAY': 3,
        'EVEN_FRIDAY': 4,
        'EVEN_SATURDAY': 5,
        'EVEN_SUNDAY': 6,
    }
    
    for pattern in patterns:
        # Check individual days (SUNDAY, MONDAY, etc.)
        if pattern in day_map and day_of_week == day_map[pattern]:
            return True
        
        # Check odd day patterns (ODD_SUNDAY, ODD_MONDAY, etc.)
        if pattern in odd_patterns and day_of_week == odd_patterns[pattern]:
            day_number = get_day_number_of_month(check_date, day_of_week)
            if day_number in [1, 3, 5]:
                return True
        
        # Check even day patterns (EVEN_SUNDAY, EVEN_MONDAY, etc.)
        if pattern in even_patterns and day_of_week == even_patterns[pattern]:
            day_number = get_day_number_of_month(check_date, day_of_week)
            if day_number in [2, 4]:
                return True
    
    return False

