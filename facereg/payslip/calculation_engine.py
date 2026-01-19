"""
Calculation Engine for Payslip Field Values

This module handles the calculation of payslip field values based on:
- Employee data (gross_salary, absent_days, etc.)
- Field configurations (PERCENTAGE, FIXED, CALCULATION)
- Previously calculated fields (by field_code)
"""

import re
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from calendar import monthrange
from django.utils import timezone
import pytz

from regface.models import Employee, AttendanceLog

logger = logging.getLogger(__name__)


def _pair_overnight_checkins_checkouts(checkin_logs, checkout_logs, user_tz):
    """
    Helper function to pair checkins and checkouts that may span midnight (overnight shifts).
    Handles both normal shifts and consecutive overnight shifts correctly.
    
    For overnight shifts (e.g., 22:00 - 06:00):
    - Checkin on Jan 15 23:00 should pair with checkout on Jan 16 05:00
    - Checkout on Jan 16 05:00 should pair with checkin on Jan 15 23:00
    
    For consecutive overnight shifts:
    - Jan 15 22:00 checkin pairs with Jan 16 06:00 checkout
    - Jan 16 22:00 checkin pairs with Jan 17 06:00 checkout
    - Each checkout is only paired once (handled by used_checkouts set)
    
    Args:
        checkin_logs: List of checkin AttendanceLog objects (sorted by timestamp)
        checkout_logs: List of checkout AttendanceLog objects (sorted by timestamp)
        user_tz: pytz timezone object
    
    Returns:
        List of tuples: [(checkin_log, checkout_log), ...] for valid pairs
    """
    pairs = []
    used_checkins = set()
    used_checkouts = set()
    
    # Convert timestamps to timezone-aware if needed
    def get_local_date(log):
        ts = log.timestamp
        if timezone.is_naive(ts):
            ts = timezone.make_aware(ts, pytz.UTC)
        return ts.astimezone(user_tz).date()
    
    def get_local_datetime(log):
        ts = log.timestamp
        if timezone.is_naive(ts):
            ts = timezone.make_aware(ts, pytz.UTC)
        return ts.astimezone(user_tz)
    
    # Process checkins in chronological order to handle consecutive shifts correctly
    for checkin in checkin_logs:
        if checkin.id in used_checkins:
            continue
        checkin_date = get_local_date(checkin)
        checkin_dt = get_local_datetime(checkin)
        
        # Find best matching checkout
        # Priority: 1) Same date, 2) Next date (overnight shift), 3) Minimum time difference
        best_checkout = None
        best_checkout_idx = None
        best_score = None  # Lower score is better (prefer same date, then shorter duration)
        
        for idx, checkout in enumerate(checkout_logs):
            if checkout.id in used_checkouts:
                continue
            checkout_date = get_local_date(checkout)
            checkout_dt = get_local_datetime(checkout)
            
            # Checkout must be after checkin
            if checkout_dt <= checkin_dt:
                continue
            
            # Calculate time difference
            time_diff = (checkout_dt - checkin_dt).total_seconds()
            
            # For overnight shifts, allow up to 30 hours (to handle late checkouts)
            # Normal shifts should be within 24 hours, but allow buffer for edge cases
            if time_diff > 30 * 3600:  # More than 30 hours, skip (likely wrong pairing)
                continue
            
            # Prefer checkout on same date (normal shift), but allow next day (overnight shift)
            # Score: 0 = same date, 1 = next date, + time difference in hours for tie-breaking
            if checkout_date == checkin_date:
                # Same date (normal shift) - prefer this
                score = 0 + (time_diff / 3600.0)  # Add hours as tie-breaker
            elif checkout_date == checkin_date + timedelta(days=1):
                # Next date (overnight shift) - acceptable
                score = 1 + (time_diff / 3600.0)  # Add hours as tie-breaker
            else:
                # More than 1 day apart - skip (likely wrong pairing)
                continue
            
            # Select checkout with best score (lower is better)
            if best_score is None or score < best_score:
                best_score = score
                best_checkout = checkout
                best_checkout_idx = idx
        
        if best_checkout:
            pairs.append((checkin, best_checkout))
            used_checkins.add(checkin.id)
            used_checkouts.add(best_checkout.id)
    
    return pairs


class CalculationError(Exception):
    """Custom exception for calculation errors"""
    pass


def _get_timezone_for_employee(employee):
    """
    Get timezone for employee based on location or default to Asia/Kolkata.
    """
    try:
        from regface.views import get_timezone_from_location, get_user_timezone
        if employee.location:
            return get_timezone_from_location(employee.location)
        else:
            # Default to Asia/Kolkata if no location
            return pytz.timezone('Asia/Kolkata')
    except ImportError:
        # Fallback to Asia/Kolkata if functions not available
        return pytz.timezone('Asia/Kolkata')


def calculate_attendance(employee, month):
    """
    Enhanced attendance calculation considering:
    - Manual Attendance (highest priority)
    - Leave Requests (approved)
    - Holidays
    - Weekoffs
    - Face Recognition Logs (with overnight shift support)
    
    Args:
        employee: Employee instance
        month: Month string in format 'YYYY-MM'
    
    Returns:
        dict: {
            'present_days': float,  # Can be decimal (28.5)
            'absent_days': float,   # Can be decimal (1.5)
            'paid_leave_days': int,
            'unpaid_leave_days': int,
            'holiday_count': int,
            'weekoff_count': int,
            'working_days': int,
            'total_days': int
        }
    """
    try:
        year, month_num = map(int, month.split('-'))
        start_date = datetime(year, month_num, 1).date()
        _, last_day = monthrange(year, month_num)
        end_date = datetime(year, month_num, last_day).date()
        
        # Get timezone for employee (for overnight shift handling)
        user_tz = _get_timezone_for_employee(employee)
        
        # Initialize counters (use Decimal for half-day support)
        present_days = Decimal('0')
        absent_days = Decimal('0')
        paid_leave_days = 0
        unpaid_leave_days = 0
        holiday_count = 0
        weekoff_count = 0
        
        # Import leave models
        try:
            from leave.models import ManualAttendance, LeaveRequest, Holiday
            from leave.utils import is_weekoff_day
        except ImportError:
            # If leave module not available, use duration-based calculation (same as attendance report)
            # Get all attendance logs (both checkin and checkout) - extend range for overnight shifts
            # Include logs from day before and day after to handle overnight shifts
            extended_start = start_date - timedelta(days=1)
            extended_end = end_date + timedelta(days=1)
            all_logs = AttendanceLog.objects.filter(
                employee=employee,
                timestamp__date__gte=extended_start,
                timestamp__date__lte=extended_end
            ).order_by('timestamp')
            
            # Group logs by date in user's timezone (for overnight shift handling)
            logs_by_date = {}
            for log in all_logs:
                log_ts = log.timestamp
                if timezone.is_naive(log_ts):
                    log_ts = timezone.make_aware(log_ts, pytz.UTC)
                log_date = log_ts.astimezone(user_tz).date()
                if log_date not in logs_by_date:
                    logs_by_date[log_date] = []
                logs_by_date[log_date].append(log)
            
            # Calculate working days (exclude weekends at minimum)
            total_days = (end_date - start_date).days + 1
            working_days = 0
            current = start_date
            while current <= end_date:
                if current.weekday() < 6:  # Monday=0, Sunday=6
                    working_days += 1
                current += timedelta(days=1)
            
            # Process each day with duration-based logic (handles overnight shifts)
            for current_date in [d for d in logs_by_date.keys() if start_date <= d <= end_date]:
                date_logs = logs_by_date.get(current_date, [])
                
                # For overnight shifts, we need to check logs from previous day AND next day
                prev_date = current_date - timedelta(days=1)
                next_date = current_date + timedelta(days=1)
                prev_date_logs = logs_by_date.get(prev_date, [])
                next_date_logs = logs_by_date.get(next_date, [])
                
                # Combine logs from previous, current, and next date for pairing
                all_relevant_logs = prev_date_logs + date_logs + next_date_logs
                
                # Separate checkins and checkouts, sorted by timestamp
                checkin_logs = sorted([log for log in all_relevant_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
                checkout_logs = sorted([log for log in all_relevant_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
                
                # Use overnight shift pairing function (same as attendance report)
                valid_pairs = _pair_overnight_checkins_checkouts(checkin_logs, checkout_logs, user_tz)
                
                # Filter pairs where checkin is on current date (for overnight shifts, checkin date is the shift date)
                pairs_for_this_date = []
                for checkin_log, checkout_log in valid_pairs:
                    checkin_ts = checkin_log.timestamp
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                    checkin_date_local = checkin_ts.astimezone(user_tz).date()
                    
                    # Include pair if checkin is on current date (this is the shift start date)
                    if checkin_date_local == current_date:
                        pairs_for_this_date.append((checkin_log, checkout_log))
                
                # Calculate total duration using the paired logs (handles overnight shifts correctly)
                total_worked_seconds = 0
                for checkin_log, checkout_log in pairs_for_this_date:
                    checkin_ts = checkin_log.timestamp
                    checkout_ts = checkout_log.timestamp
                    
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                    if timezone.is_naive(checkout_ts):
                        checkout_ts = timezone.make_aware(checkout_ts, pytz.UTC)
                    
                    pair_duration = (checkout_ts - checkin_ts).total_seconds()
                    if pair_duration > 0:
                        total_worked_seconds += pair_duration
                
                # Determine status based on duration (same as attendance report)
                if not pairs_for_this_date and not date_logs:
                    # No logs for this date = absent (only count if it's a weekday)
                    if current_date.weekday() < 6:  # Not a weekend
                        absent_days += Decimal('1')
                elif total_worked_seconds < 3 * 3600:
                    # < 3 hours = absent
                    absent_days += Decimal('1')
                elif total_worked_seconds < 6 * 3600:
                    # 3-6 hours = half day
                    present_days += Decimal('0.5')
                    absent_days += Decimal('0.5')
                else:
                    # > 6 hours = present
                    present_days += Decimal('1')
            
            # Count days without any logs as absent (only weekdays)
            current_date = start_date
            while current_date <= end_date:
                if current_date not in logs_by_date:
                    # Check if it's a weekend
                    if current_date.weekday() < 6:  # Not a weekend (Monday=0, Sunday=6)
                        absent_days += Decimal('1')
                current_date += timedelta(days=1)
            
            # Validation: If employee has 0 present days, all working days should be absent
            if present_days == 0:
                absent_days = Decimal(str(working_days))
            
            return {
                'present_days': float(present_days),
                'absent_days': float(absent_days),
                'paid_leave_days': 0,
                'unpaid_leave_days': 0,
                'holiday_count': 0,
                'weekoff_count': 0,
                'working_days': working_days,
                'total_days': total_days
            }
        
        # Get all relevant data for the month
        manual_attendances = ManualAttendance.objects.filter(
            employee=employee,
            attendance_date__gte=start_date,
            attendance_date__lte=end_date
        ).select_related('leave_request__leave_type')
        
        approved_leaves = LeaveRequest.objects.filter(
            employee=employee,
            status='APPROVED',
            start_date__lte=end_date,
            end_date__gte=start_date
        ).select_related('leave_type')
        
        holidays = Holiday.objects.filter(
            location=employee.location,
            holiday_date__gte=start_date,
            holiday_date__lte=end_date
        )
        
        # Get all attendance logs (both checkin and checkout) for duration calculation
        # Extend range to include previous and next day for overnight shift handling
        extended_start = start_date - timedelta(days=1)
        extended_end = end_date + timedelta(days=1)
        all_attendance_logs = AttendanceLog.objects.filter(
            employee=employee,
            timestamp__date__gte=extended_start,
            timestamp__date__lte=extended_end
        ).order_by('timestamp')
        
        # Group logs by date in user's timezone (for overnight shift handling)
        logs_by_date = {}
        for log in all_attendance_logs:
            log_ts = log.timestamp
            if timezone.is_naive(log_ts):
                log_ts = timezone.make_aware(log_ts, pytz.UTC)
            log_date = log_ts.astimezone(user_tz).date()
            if log_date not in logs_by_date:
                logs_by_date[log_date] = []
            logs_by_date[log_date].append(log)
        
        # Create lookup dictionaries
        manual_dict = {ma.attendance_date: ma for ma in manual_attendances}
        holiday_set = {h.holiday_date for h in holidays}
        
        # Create leave date set
        leave_dates = {}
        for leave in approved_leaves:
            current = leave.start_date
            while current <= leave.end_date:
                if start_date <= current <= end_date:
                    leave_dates[current] = leave
                current += timedelta(days=1)
        
        # Iterate through each day of the month
        current_date = start_date
        while current_date <= end_date:
            # Priority 1: Manual Attendance
            if current_date in manual_dict:
                manual = manual_dict[current_date]
                if manual.status == 'PRESENT':
                    present_days += Decimal('1')
                elif manual.status == 'ABSENT':
                    absent_days += Decimal('1')
                elif manual.status == 'HALF_DAY':
                    present_days += Decimal('0.5')  # Half day present
                    absent_days += Decimal('0.5')   # Half day absent
                elif manual.status == 'HOLIDAY':
                    holiday_count += 1
                elif manual.status == 'WEEKOFF':
                    weekoff_count += 1
                elif manual.status == 'LEAVE':
                    # Check if linked to leave request
                    if manual.leave_request:
                        if manual.leave_request.leave_type.is_paid:
                            paid_leave_days += 1
                        else:
                            unpaid_leave_days += 1
            
            # Priority 2: Approved Leave
            elif current_date in leave_dates:
                leave = leave_dates[current_date]
                if leave.leave_type.is_paid:
                    paid_leave_days += 1
                else:
                    unpaid_leave_days += 1
            
            # Priority 3: Holiday
            elif current_date in holiday_set:
                holiday_count += 1
            
            # Priority 4: Weekoff
            elif is_weekoff_day(employee, current_date):
                weekoff_count += 1
            
            # Priority 5: Face Recognition Log (with duration-based calculation and overnight shift support)
            elif current_date in logs_by_date or (current_date - timedelta(days=1)) in logs_by_date or (current_date + timedelta(days=1)) in logs_by_date:
                date_logs = logs_by_date.get(current_date, [])
                
                # For overnight shifts, we need to check logs from previous day AND next day
                prev_date = current_date - timedelta(days=1)
                next_date = current_date + timedelta(days=1)
                prev_date_logs = logs_by_date.get(prev_date, [])
                next_date_logs = logs_by_date.get(next_date, [])
                
                # Combine logs from previous, current, and next date for pairing
                all_relevant_logs = prev_date_logs + date_logs + next_date_logs
                
                # Separate checkins and checkouts, sorted by timestamp
                checkin_logs = sorted([log for log in all_relevant_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
                checkout_logs = sorted([log for log in all_relevant_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
                
                # Use overnight shift pairing function (same as attendance report)
                valid_pairs = _pair_overnight_checkins_checkouts(checkin_logs, checkout_logs, user_tz)
                
                # Filter pairs where checkin is on current date (for overnight shifts, checkin date is the shift date)
                pairs_for_this_date = []
                for checkin_log, checkout_log in valid_pairs:
                    checkin_ts = checkin_log.timestamp
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                    checkin_date_local = checkin_ts.astimezone(user_tz).date()
                    
                    # Include pair if checkin is on current date (this is the shift start date)
                    if checkin_date_local == current_date:
                        pairs_for_this_date.append((checkin_log, checkout_log))
                
                # Calculate total duration using the paired logs (handles overnight shifts correctly)
                total_worked_seconds = 0
                for checkin_log, checkout_log in pairs_for_this_date:
                    checkin_ts = checkin_log.timestamp
                    checkout_ts = checkout_log.timestamp
                    
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                    if timezone.is_naive(checkout_ts):
                        checkout_ts = timezone.make_aware(checkout_ts, pytz.UTC)
                    
                    pair_duration = (checkout_ts - checkin_ts).total_seconds()
                    if pair_duration > 0:
                        total_worked_seconds += pair_duration
                
                # Determine status based on duration (same as attendance report)
                if not pairs_for_this_date:
                    # No valid pairs for this date = absent
                    absent_days += Decimal('1')
                elif total_worked_seconds < 3 * 3600:
                    # < 3 hours = absent
                    absent_days += Decimal('1')
                elif total_worked_seconds < 6 * 3600:
                    # 3-6 hours = half day
                    present_days += Decimal('0.5')
                    absent_days += Decimal('0.5')
                else:
                    # > 6 hours = present
                    present_days += Decimal('1')
            
            # Priority 6: Absent
            else:
                absent_days += Decimal('1')
            
            current_date += timedelta(days=1)
        
        # Calculate working days (exclude holidays and weekoffs)
        total_days = (end_date - start_date).days + 1
        working_days = total_days - holiday_count - weekoff_count
        
        # CRITICAL VALIDATION: If employee has 0 present days and no paid leaves, 
        # ALL working days must be counted as absent
        # This handles cases where:
        # 1. Employee didn't show up at all
        # 2. Employee marked attendance but didn't meet work threshold (< 3 hours per day)
        if float(present_days) == 0.0 and paid_leave_days == 0:
            absent_days = Decimal(str(working_days))
        else:
            # Ensure all working days are accounted for
            # Calculate total accounted days
            total_accounted = present_days + absent_days + Decimal(str(paid_leave_days)) + Decimal(str(unpaid_leave_days))
            
            if total_accounted < working_days:
                # If there's a discrepancy (some days not accounted for), add remaining days to absent_days
                # This ensures: present_days + absent_days + paid_leave_days + unpaid_leave_days = working_days
                absent_days += (Decimal(str(working_days)) - total_accounted)
            elif total_accounted > working_days:
                # If over-accounted (shouldn't happen, but handle gracefully), adjust absent_days
                excess = total_accounted - Decimal(str(working_days))
                if absent_days >= excess:
                    absent_days -= excess
        
        return {
            'present_days': float(present_days),  # Can be decimal like 28.5
            'absent_days': float(absent_days),    # Can be decimal like 1.5
            'paid_leave_days': paid_leave_days,
            'unpaid_leave_days': unpaid_leave_days,
            'holiday_count': holiday_count,
            'weekoff_count': weekoff_count,
            'working_days': working_days,
            'total_days': total_days
        }
        
    except (ValueError, AttributeError) as e:
        # If calculation fails, return defaults
        return {
            'present_days': 0.0,
            'absent_days': 0.0,
            'paid_leave_days': 0,
            'unpaid_leave_days': 0,
            'holiday_count': 0,
            'weekoff_count': 0,
            'working_days': 30,  # Default to 30 days
            'total_days': 30
        }


def evaluate_formula(formula, context):
    """
    Evaluate a calculation formula string safely.
    
    Supports special keywords for pro-rated calculations:
    - BASIC_EARNED: Pro-rated Basic Salary (Basic * present_days / working_days)
    - GROSS_EARNED: Pro-rated Gross Salary (Gross * present_days / working_days)
    - BASIC_FULL: Full Basic Salary (same as BASIC)
    - GROSS_FULL: Full Gross Salary (same as gross_salary)
    
    Args:
        formula: String formula (e.g., "BASIC * 0.40", "BASIC_EARNED * 0.12", "absent_days * deduction_per_day")
        context: Dictionary with variable values (e.g., {'BASIC': 30000, 'absent_days': 3})
    
    Returns:
        Decimal: Calculated result
    """
    if not formula or not formula.strip():
        raise CalculationError("Empty formula")
    
    formula = formula.strip()
    formula_upper = formula.upper()
    
    # Special case: If formula is just a variable name (no operators), return value directly
    for key in context.keys():
        if key.upper() == formula_upper:
            return Decimal(str(context[key]))
    
    # Handle special keywords for pro-rated calculations
    # Calculate BASIC_EARNED if needed
    if 'BASIC_EARNED' in formula_upper:
        if 'BASIC' in context:
            basic_full = Decimal(str(context['BASIC']))
            if 'present_days' in context and 'working_days' in context:
                working_days = Decimal(str(context['working_days']))
                present_days = Decimal(str(context['present_days']))
                if working_days > 0:
                    basic_earned = basic_full * (present_days / working_days)
                    context['BASIC_EARNED'] = float(basic_earned)
                else:
                    context['BASIC_EARNED'] = float(basic_full)
            else:
                context['BASIC_EARNED'] = float(basic_full)
        else:
            # If BASIC not calculated yet, use gross_salary as fallback
            if 'gross_salary' in context:
                gross_full = Decimal(str(context['gross_salary']))
                if 'present_days' in context and 'working_days' in context:
                    working_days = Decimal(str(context['working_days']))
                    present_days = Decimal(str(context['present_days']))
                    if working_days > 0:
                        # Estimate Basic as 60% of gross (common ratio)
                        basic_estimated = gross_full * Decimal('0.60')
                        basic_earned = basic_estimated * (present_days / working_days)
                        context['BASIC_EARNED'] = float(basic_earned)
                    else:
                        context['BASIC_EARNED'] = float(gross_full * Decimal('0.60'))
                else:
                    context['BASIC_EARNED'] = float(gross_full * Decimal('0.60'))
            else:
                raise CalculationError("BASIC_EARNED requires BASIC or gross_salary in context")
    
    # Calculate GROSS_EARNED if needed
    if 'GROSS_EARNED' in formula_upper:
        if 'gross_salary' in context:
            gross_full = Decimal(str(context['gross_salary']))
            if 'present_days' in context and 'working_days' in context:
                working_days = Decimal(str(context['working_days']))
                present_days = Decimal(str(context['present_days']))
                if working_days > 0:
                    gross_earned = gross_full * (present_days / working_days)
                    context['GROSS_EARNED'] = float(gross_earned)
                else:
                    context['GROSS_EARNED'] = float(gross_full)
            else:
                context['GROSS_EARNED'] = float(gross_full)
        else:
            raise CalculationError("GROSS_EARNED requires gross_salary in context")
    
    # BASIC_FULL is same as BASIC
    if 'BASIC_FULL' in formula_upper and 'BASIC' in context:
        context['BASIC_FULL'] = context['BASIC']
    
    # GROSS_FULL is same as gross_salary
    if 'GROSS_FULL' in formula_upper and 'gross_salary' in context:
        context['GROSS_FULL'] = context['gross_salary']
    
    # Replace variable names with their values from context
    # First, sort by length (longest first) to avoid partial replacements
    sorted_keys = sorted(context.keys(), key=len, reverse=True)
    
    for key in sorted_keys:
        if key in context:
            value = context[key]
            # Skip non-numeric values (like 'month' which is a string)
            # Only replace numeric values in formulas
            try:
                # Try to convert to float to ensure it's numeric
                numeric_value = float(value)
                # Use word boundaries to ensure exact matches (case-insensitive)
                # This handles cases where formula uses "ABSENT_DAYS" but context has "absent_days"
                pattern = r'\b' + re.escape(str(key)) + r'\b'
                formula = re.sub(pattern, str(numeric_value), formula, flags=re.IGNORECASE)
            except (ValueError, TypeError):
                # Skip non-numeric values (like 'month', 'employee_id', etc.)
                # These shouldn't be used in numeric calculations
                continue
    
    # Replace common operations
    formula = formula.replace('*', '*').replace('/', '/').replace('+', '+').replace('-', '-')
    
    # Validate that only safe characters remain
    # Allow: numbers, decimals, operators, parentheses, spaces
    safe_pattern = r'^[0-9.\+\-\*\/\(\)\s]+$'
    if not re.match(safe_pattern, formula):
        raise CalculationError(f"Invalid characters in formula: {formula}")
    
    try:
        # Use eval with limited scope for safety
        # In production, consider using a proper expression parser like simpleeval
        result = eval(formula, {"__builtins__": {}}, {})
        return Decimal(str(result)).quantize(Decimal('0.01'))
    except Exception as e:
        raise CalculationError(f"Error evaluating formula '{formula}': {str(e)}")


def calculate_field_value(field, context, employee=None):
    """
    Calculate the value for a single payslip field.
    
    Standard Payslip Format:
    - Earnings: Always show full monthly amounts (not pro-rated)
    - Deductions: PF/ESI calculated on full amounts, LOP calculated separately
    - Net Pay = Gross Salary - All Deductions
    
    Args:
        field: PayslipField instance
        context: Dictionary with available variables for calculation
        employee: Employee instance (optional, needed for resolving employee attributes in INFO fields)
    
    Returns:
        Decimal or str: Calculated field value (Decimal for numeric, str for INFO text fields)
    """
    value_type = field.value_type
    value_str = field.value
    field_type = field.field_type  # EARNING, DEDUCTION, or INFO
    
    try:
        if value_type == 'PERCENTAGE':
            # Percentage calculation
            percentage = Decimal(value_str)
            
            # For EARNING fields: Calculate percentage of full gross_salary
            # For DEDUCTION fields: Calculate percentage of full gross_salary or BASIC (depending on field)
            if 'gross_salary' not in context:
                raise CalculationError("gross_salary not found in context for percentage calculation")
            
            # Use full gross_salary (not pro-rated) for all percentage calculations
            # This ensures earnings and deductions (PF/ESI) are calculated on full amounts
            gross = Decimal(str(context['gross_salary']))  # Full gross salary
            result = (gross * percentage / 100).quantize(Decimal('0.01'))
            logger.debug(f"    PERCENTAGE: {gross} * {percentage}% / 100 = {result} (using FULL gross_salary)")
            return result
        
        elif value_type == 'FIXED':
            # Fixed amount calculation
            # EARNING fields: Always show full fixed amount (not pro-rated)
            # DEDUCTION fields: Always show full fixed amount (not pro-rated)
            # INFO fields: Return the fixed value as-is (usually a reference to context variable)
            if field_type == 'INFO':
                # For INFO fields, the value might be:
                # 1. A reference to a context variable (e.g., "present_days")
                # 2. An employee attribute reference (e.g., "employee.department")
                # 3. A literal string or number
                
                # First, check if it's an employee attribute reference (e.g., "employee.department")
                if value_str.startswith('employee.') and employee:
                    attr_name = value_str.replace('employee.', '').strip()
                    try:
                        # Handle special case: employee_id might refer to employee_code or id
                        if attr_name == 'employee_id':
                            # Try employee_code first (preferred), then id as fallback
                            employee_code = getattr(employee, 'employee_code', None)
                            print(f"    DEBUG: employee.employee_code = {employee_code} (type: {type(employee_code)})")
                            if employee_code and str(employee_code).strip():  # Check if not None and not empty
                                result = str(employee_code).strip()
                                print(f"    INFO FIXED: Using employee_code = '{result}'")
                                logger.debug(f"    INFO FIXED: Resolved employee.employee_id from employee_code = {result}")
                                return result
                            else:
                                # Fallback to id (UUID) - but format it nicely
                                employee_id = getattr(employee, 'id', None)
                                print(f"    DEBUG: employee.id = {employee_id} (type: {type(employee_id)})")
                                if employee_id:
                                    # For UUID, just return the string representation
                                    result = str(employee_id)
                                    print(f"    INFO FIXED: Using id (UUID) = '{result}' (employee_code was empty)")
                                    logger.debug(f"    INFO FIXED: Resolved employee.employee_id from id (UUID) = {result}")
                                    return result
                                else:
                                    print(f"    WARNING: No employee_code or id found")
                                    logger.warning(f"    INFO FIXED: employee.employee_id not found (no employee_code or id)")
                                    return '—'  # Use dash for missing values
                        else:
                            # Use getattr to safely get the attribute value
                            attr_value = getattr(employee, attr_name, None)
                            print(f"    DEBUG: employee.{attr_name} = {attr_value} (type: {type(attr_value)})")
                            
                            # Check if value exists and is not empty
                            if attr_value is not None:
                                # For string fields, check if it's not just whitespace
                                if isinstance(attr_value, str):
                                    attr_value = attr_value.strip()
                                    if attr_value:  # Not empty after strip
                                        print(f"    INFO FIXED: Using employee.{attr_name} = '{attr_value}'")
                                        logger.debug(f"    INFO FIXED: Resolved employee attribute '{attr_name}' = '{attr_value}'")
                                        return attr_value
                                    else:
                                        print(f"    INFO FIXED: employee.{attr_name} is empty string, returning '—'")
                                        logger.debug(f"    INFO FIXED: Employee attribute '{attr_name}' is empty string")
                                        return '—'  # Use dash for empty values
                                else:
                                    # For non-string values (numbers, dates, etc.)
                                    result = str(attr_value)
                                    print(f"    INFO FIXED: Using employee.{attr_name} = '{result}'")
                                    logger.debug(f"    INFO FIXED: Resolved employee attribute '{attr_name}' = {result}")
                                    return result
                            else:
                                print(f"    INFO FIXED: employee.{attr_name} is None, returning '—'")
                                logger.debug(f"    INFO FIXED: Employee attribute '{attr_name}' is None")
                                return '—'  # Use dash for None values
                    except AttributeError:
                        logger.warning(f"    INFO FIXED: Employee attribute '{attr_name}' does not exist on Employee model")
                        return '—'  # Use dash for missing attributes
                    except Exception as e:
                        logger.error(f"    INFO FIXED: Error accessing employee attribute '{attr_name}': {str(e)}")
                        return '—'  # Use dash for errors
                
                # Second, check if it's a variable name in context (case-insensitive)
                value_str_lower = value_str.lower().strip()
                for key, val in context.items():
                    if key.lower() == value_str_lower:
                        # Return the value as-is (could be int, float, or string)
                        # For numeric values, return as Decimal for consistency
                        logger.debug(f"    INFO FIXED: Found '{value_str}' in context as '{key}' = {val}")
                        if isinstance(val, (int, float)):
                            return Decimal(str(val))
                        else:
                            # For non-numeric values (strings), return as string
                            return val
                
                # Third, try to parse as number
                try:
                    result = Decimal(str(value_str)).quantize(Decimal('0.01'))
                    logger.debug(f"    INFO FIXED: Parsed '{value_str}' as number: {result}")
                    return result
                except (ValueError, InvalidOperation):
                    # If it's not a number and not in context, return the string value as-is
                    logger.debug(f"    INFO FIXED: Using '{value_str}' as string (not in context, not a number, not employee attribute)")
                    return value_str
            else:
                # For EARNING and DEDUCTION: Parse as Decimal and use full fixed amount
                try:
                    fixed_amount = Decimal(value_str)
                    result = fixed_amount.quantize(Decimal('0.01'))
                    logger.debug(f"    FIXED: Using full fixed amount: {result} (not pro-rated)")
                    return result
                except (ValueError, InvalidOperation):
                    raise CalculationError(f"Invalid fixed amount '{value_str}' for {field_type} field")
        
        elif value_type == 'CALCULATION':
            # Formula-based calculation (e.g., "absent_days * deduction_per_day" for LOP)
            # This allows flexible calculations like LOP = absent_days * (gross_salary / working_days)
            logger.debug(f"    CALCULATION: Evaluating formula: {value_str}")
            result = evaluate_formula(value_str, context)
            logger.debug(f"    CALCULATION: Result: {result}")
            return result
        
        else:
            raise CalculationError(f"Unknown value_type: {value_type}")
    
    except (ValueError, InvalidOperation) as e:
        raise CalculationError(f"Invalid value '{value_str}' for {value_type}: {str(e)}")


def calculate_payslip_fields(employee, field_config, month):
    """
    Calculate all payslip field values for an employee.
    
    Args:
        employee: Employee instance
        field_config: PayslipFieldConfig instance
        month: Month string in format 'YYYY-MM'
    
    Returns:
        dict: {
            'field_values': {field_code: calculated_value, ...},
            'total_earnings': Decimal,
            'total_deductions': Decimal,
            'attendance': {'present_days': int, 'absent_days': int, 'working_days': int}
        }
    """
    # Get attendance data
    print(f"\n{'='*60}")
    print(f"PAYSLIP CALCULATION START")
    print(f"Employee: {employee.name} (ID: {employee.id})")
    print(f"Month: {month}")
    print(f"Location: {employee.location.name if employee.location else 'N/A'}")
    logger.info(f"=== PAYSLIP CALCULATION START ===")
    logger.info(f"Employee: {employee.name} (ID: {employee.id})")
    logger.info(f"Month: {month}")
    logger.info(f"Location: {employee.location.name if employee.location else 'N/A'}")
    
    attendance = calculate_attendance(employee, month)
    print(f"\n--- ATTENDANCE CALCULATION ---")
    print(f"Present Days: {attendance['present_days']}")
    print(f"Absent Days: {attendance['absent_days']}")
    print(f"Paid Leave Days: {attendance['paid_leave_days']}")
    print(f"Unpaid Leave Days: {attendance['unpaid_leave_days']}")
    print(f"Working Days: {attendance['working_days']}")
    print(f"Total Days: {attendance['total_days']}")
    print(f"Holiday Count: {attendance['holiday_count']}")
    print(f"Weekoff Count: {attendance['weekoff_count']}")
    logger.info(f"--- ATTENDANCE CALCULATION ---")
    logger.info(f"Present Days: {attendance['present_days']}")
    logger.info(f"Absent Days: {attendance['absent_days']}")
    logger.info(f"Paid Leave Days: {attendance['paid_leave_days']}")
    logger.info(f"Unpaid Leave Days: {attendance['unpaid_leave_days']}")
    logger.info(f"Working Days: {attendance['working_days']}")
    logger.info(f"Total Days: {attendance['total_days']}")
    logger.info(f"Holiday Count: {attendance['holiday_count']}")
    logger.info(f"Weekoff Count: {attendance['weekoff_count']}")
    
    # Get gross salary (full monthly amount)
    # Priority: gross_salary > base_salary > 0
    # If gross_salary is None/0, use base_salary (which has default 30000)
    print(f"\n--- GROSS SALARY DETERMINATION ---")
    print(f"employee.gross_salary: {employee.gross_salary}")
    print(f"employee.base_salary: {employee.base_salary}")
    logger.info(f"--- GROSS SALARY DETERMINATION ---")
    logger.info(f"employee.gross_salary: {employee.gross_salary}")
    logger.info(f"employee.base_salary: {employee.base_salary}")
    
    if employee.gross_salary and employee.gross_salary > 0:
        gross_salary = employee.gross_salary
        print(f"✓ Using employee.gross_salary: {gross_salary}")
        logger.info(f"Using employee.gross_salary: {gross_salary}")
    elif employee.base_salary and employee.base_salary > 0:
        gross_salary = employee.base_salary
        print(f"✓ Using employee.base_salary (fallback): {gross_salary}")
        logger.info(f"Using employee.base_salary (fallback): {gross_salary}")
    else:
        # Fallback: use base_salary default or 0
        gross_salary = employee.base_salary if employee.base_salary else Decimal('0')
        print(f"✓ Using fallback gross_salary: {gross_salary}")
        logger.info(f"Using fallback gross_salary: {gross_salary}")
    
    print(f"FINAL GROSS SALARY: {gross_salary}")
    logger.info(f"FINAL GROSS SALARY: {gross_salary}")
    # Calculate net payable days (days for which employee should be paid)
    # Net payable = present_days + paid_leave_days (holidays and weekoffs are already excluded from working_days)
    present_days = Decimal(str(attendance['present_days']))
    paid_leave_days = Decimal(str(attendance['paid_leave_days']))
    net_payable_days = present_days + paid_leave_days
    
    print(f"\n--- NET PAYABLE DAYS CALCULATION ---")
    print(f"present_days (Decimal): {present_days}")
    print(f"paid_leave_days (Decimal): {paid_leave_days}")
    print(f"net_payable_days = {present_days} + {paid_leave_days} = {net_payable_days}")
    logger.info(f"--- NET PAYABLE DAYS CALCULATION ---")
    logger.info(f"present_days (Decimal): {present_days}")
    logger.info(f"paid_leave_days (Decimal): {paid_leave_days}")
    logger.info(f"net_payable_days = {present_days} + {paid_leave_days} = {net_payable_days}")
    
    # Calculate working days (excludes holidays and weekoffs)
    working_days = attendance['working_days']
    absent_days = Decimal(str(attendance['absent_days']))
    
    # Calculate deduction per day (for LOP calculation)
    # Formula: gross_salary / working_days
    if working_days > 0:
        deduction_per_day = float(gross_salary) / working_days
        logger.info(f"deduction_per_day = {gross_salary} / {working_days} = {deduction_per_day}")
    else:
        deduction_per_day = 0.0
        logger.warning(f"working_days is 0, deduction_per_day set to 0.0")
    
    # Build initial context with employee data
    # IMPORTANT: Use FULL gross_salary (not pro-rated) for all calculations
    # Earnings will show full amounts, deductions will be calculated on full amounts
    # LOP will be calculated separately as: absent_days * deduction_per_day
    context = {
        'gross_salary': float(gross_salary),  # FULL gross salary (not pro-rated) - for standard payslip format
        'base_gross_salary': float(gross_salary),  # Same as gross_salary (for backward compatibility)
        'present_days': attendance['present_days'],  # Can be decimal (28.5)
        'absent_days': attendance['absent_days'],    # Can be decimal (1.5)
        'paid_leave_days': attendance['paid_leave_days'],
        'unpaid_leave_days': attendance['unpaid_leave_days'],
        'holiday_count': attendance['holiday_count'],
        'weekoff_count': attendance['weekoff_count'],
        'working_days': attendance['working_days'],
        'total_days': attendance['total_days'],
        'net_payable_days': float(net_payable_days),  # Days for which employee should be paid
        'deduction_per_day': deduction_per_day,  # Auto-calculated: gross_salary / working_days (for LOP)
        'month': month,
        'base_salary': float(employee.base_salary or Decimal('0')),
    }
    
    logger.info(f"--- INITIAL CONTEXT ---")
    logger.info(f"context['gross_salary']: {context['gross_salary']} (FULL, not pro-rated)")
    logger.info(f"context['present_days']: {context['present_days']}")
    logger.info(f"context['absent_days']: {context['absent_days']}")
    logger.info(f"context['net_payable_days']: {context['net_payable_days']}")
    logger.info(f"context['deduction_per_day']: {context['deduction_per_day']}")
    
    # Get all fields ordered by display_order
    fields = field_config.fields.filter(is_deleted=False).order_by('display_order')
    
    field_values = {}
    total_earnings = Decimal('0')
    total_deductions = Decimal('0')
    
    # Check if LOP (Loss of Pay) deduction field exists
    has_lop_field = False
    lop_field_code = None
    for field in fields:
        if field.is_visible and field.field_type == 'DEDUCTION':
            # Check if this is an LOP field (common field codes: ABSENT_DEDUCTION, LOP, LOSS_OF_PAY)
            field_code_upper = field.field_code.upper()
            if 'ABSENT' in field_code_upper or 'LOP' in field_code_upper or 'LOSS' in field_code_upper:
                has_lop_field = True
                lop_field_code = field.field_code
                break
    
    logger.info(f"--- FIELD CONFIGURATION ---")
    logger.info(f"Total fields: {fields.count()}")
    logger.info(f"Has LOP field: {has_lop_field}, LOP field code: {lop_field_code}")
    
    # Calculate fields in order (so later fields can reference earlier ones)
    # IMPORTANT: If present_days = 0, skip all deductions except LOP
    # When employee has 0 present days, only LOP should be deducted (full gross_salary)
    present_days_value = Decimal(str(attendance['present_days']))
    skip_deductions = (present_days_value == 0)
    
    logger.info(f"--- FIELD CALCULATION START ---")
    logger.info(f"present_days_value: {present_days_value}, skip_deductions: {skip_deductions}")
    
    for field in fields:
        if not field.is_visible:
            logger.debug(f"Skipping field {field.field_code} (not visible)")
            continue
        
        # Skip deductions if present_days = 0 (except LOP which will be handled separately)
        if skip_deductions and field.field_type == 'DEDUCTION':
            # Check if this is an LOP field - if yes, skip it (we'll add full gross as LOP)
            field_code_upper = field.field_code.upper()
            if 'ABSENT' in field_code_upper or 'LOP' in field_code_upper or 'LOSS' in field_code_upper:
                # Skip LOP field - we'll add full gross as LOP instead
                logger.info(f"Skipping LOP field {field.field_code} (will be calculated separately)")
                continue
            # Skip all other deductions when present_days = 0
            logger.info(f"Skipping deduction field {field.field_code} (present_days = 0)")
            continue
        
        logger.info(f"--- Calculating field: {field.field_code} ---")
        logger.info(f"  Field Type: {field.field_type}")
        logger.info(f"  Value Type: {field.value_type}")
        logger.info(f"  Value: {field.value}")
        
        try:
            value = calculate_field_value(field, context, employee)
            logger.info(f"  Calculated Value: {value}")
            
            # Store the calculated value
            # For INFO fields, store as-is (could be string or number)
            if field.field_type == 'INFO':
                # For INFO fields, preserve the original type (could be string, number, etc.)
                if isinstance(value, Decimal):
                    # Convert Decimal to appropriate type (int if whole number, float otherwise)
                    val_float = float(value)
                    if val_float.is_integer():
                        field_values[field.field_code] = int(val_float)
                        context[field.field_code] = int(val_float)
                    else:
                        field_values[field.field_code] = val_float
                        context[field.field_code] = val_float
                elif isinstance(value, (int, float)):
                    field_values[field.field_code] = value
                    context[field.field_code] = value
                else:
                    # String or other type
                    field_values[field.field_code] = value
                    context[field.field_code] = value
            else:
                field_values[field.field_code] = float(value)
                # Add to context for subsequent calculations
                context[field.field_code] = float(value)
            
            # Update totals (INFO fields don't affect totals)
            if field.field_type == 'EARNING':
                total_earnings += value
                logger.info(f"  Added to EARNINGS. New total_earnings: {total_earnings}")
            elif field.field_type == 'DEDUCTION':
                total_deductions += value
                logger.info(f"  Added to DEDUCTIONS. New total_deductions: {total_deductions}")
            else:
                logger.info(f"  INFO field - not added to totals")
        
        except CalculationError as e:
            # Log error but continue with other fields
            logger.error(f"  ERROR calculating field {field.field_code}: {str(e)}")
            # Store 0 as default or raise if needed
            if field.field_type == 'INFO':
                field_values[field.field_code] = '—'  # Use dash for INFO fields that fail
                logger.info(f"  Set INFO field to '—' due to error")
            else:
                field_values[field.field_code] = 0.0
                context[field.field_code] = 0.0
                logger.warning(f"  Set field value to 0.0 due to error")
    
    print(f"\n--- FIELD CALCULATION COMPLETE ---")
    print(f"Total Earnings (sum of all EARNING fields): {total_earnings}")
    print(f"Total Deductions (sum of all DEDUCTION fields before LOP): {total_deductions}")
    logger.info(f"--- FIELD CALCULATION COMPLETE ---")
    logger.info(f"Total Earnings (sum of all EARNING fields): {total_earnings}")
    logger.info(f"Total Deductions (sum of all DEDUCTION fields before LOP): {total_deductions}")
    
    # Calculate final gross salary (sum of all earnings) first
    # This is needed for LOP calculation when present_days = 0
    # NOTE: actual_gross_salary = sum of earnings, which may differ from source gross_salary
    # if the field configuration doesn't add up to the full gross_salary
    if total_earnings > 0:
        actual_gross_salary = total_earnings
        print(f"\n--- GROSS SALARY COMPARISON ---")
        print(f"Source gross_salary (from employee): {gross_salary}")
        print(f"Actual gross_salary (sum of earnings): {actual_gross_salary}")
        difference = float(gross_salary) - float(actual_gross_salary)
        if abs(difference) > 0.01:  # Only show if there's a meaningful difference
            print(f"Difference: {difference:.2f}")
            print(f"NOTE: Sum of earnings ({actual_gross_salary}) differs from source gross_salary ({gross_salary})")
            print(f"      This is normal if earnings fields don't add up to full gross_salary")
        logger.info(f"Using total_earnings as actual_gross_salary: {actual_gross_salary}")
        if abs(difference) > 0.01:
            logger.info(f"WARNING: Sum of earnings ({actual_gross_salary}) differs from source gross_salary ({gross_salary}) by {difference:.2f}")
    else:
        actual_gross_salary = Decimal(str(gross_salary))
        print(f"No earnings calculated, using original gross_salary as actual_gross_salary: {actual_gross_salary}")
        logger.info(f"No earnings calculated, using original gross_salary as actual_gross_salary: {actual_gross_salary}")
    
    print(f"ACTUAL GROSS SALARY (sum of earnings): {actual_gross_salary}")
    logger.info(f"ACTUAL GROSS SALARY (sum of earnings): {actual_gross_salary}")
    
    # Add pro-rated values to context for keyword-based calculations
    # These allow formulas to use BASIC_EARNED, GROSS_EARNED, etc.
    # Note: These are calculated after all fields are processed so BASIC is available
    logger.info(f"--- PRO-RATED VALUES CALCULATION ---")
    if working_days > 0:
        # Calculate pro-rated gross (earned gross)
        gross_earned = actual_gross_salary * (present_days_value / Decimal(str(working_days)))
        context['GROSS_EARNED'] = float(gross_earned)
        context['GROSS_FULL'] = float(actual_gross_salary)
        logger.info(f"GROSS_EARNED = {actual_gross_salary} * ({present_days_value} / {working_days}) = {gross_earned}")
        logger.info(f"GROSS_FULL = {actual_gross_salary}")
        
        # Calculate pro-rated basic if BASIC field exists
        if 'BASIC' in context:
            basic_full = Decimal(str(context['BASIC']))
            basic_earned = basic_full * (present_days_value / Decimal(str(working_days)))
            context['BASIC_EARNED'] = float(basic_earned)
            context['BASIC_FULL'] = float(basic_full)
            logger.info(f"BASIC_EARNED = {basic_full} * ({present_days_value} / {working_days}) = {basic_earned}")
            logger.info(f"BASIC_FULL = {basic_full}")
    else:
        # If no working days, earned = full
        context['GROSS_EARNED'] = float(actual_gross_salary)
        context['GROSS_FULL'] = float(actual_gross_salary)
        logger.info(f"No working days, GROSS_EARNED = GROSS_FULL = {actual_gross_salary}")
        if 'BASIC' in context:
            context['BASIC_EARNED'] = float(context['BASIC'])
            context['BASIC_FULL'] = float(context['BASIC'])
            logger.info(f"BASIC_EARNED = BASIC_FULL = {context['BASIC']}")
    
    # Auto-calculate LOP if needed:
    # 1. present_days = 0: LOP = full gross_salary (sum of earnings)
    # 2. absent_days > 0: Calculate LOP based on absent days
    logger.info(f"--- LOP CALCULATION ---")
    # Check if LOP was already calculated in the loop
    lop_was_calculated = False
    existing_lop_amount = Decimal('0')
    existing_lop_code = None
    field_type_map = {f.field_code: f.field_type for f in fields}
    
    for code, val in field_values.items():
        code_upper = code.upper()
        if ('ABSENT' in code_upper or 'LOP' in code_upper or 'LOSS' in code_upper) and field_type_map.get(code) == 'DEDUCTION':
            lop_was_calculated = True
            existing_lop_amount = Decimal(str(val))
            existing_lop_code = code
            logger.info(f"Found existing LOP field: {code} = {existing_lop_amount}")
            break
    
    # Use detected LOP field code, or default to 'LOP'
    lop_field_code_to_use = lop_field_code if lop_field_code else (existing_lop_code if existing_lop_code else 'LOP')
    logger.info(f"Using LOP field code: {lop_field_code_to_use}")
    
    if present_days_value == 0:
        # Employee has 0 present days: LOP = full gross_salary (sum of earnings)
        print(f"\n--- LOP CALCULATION (present_days = 0) ---")
        print(f"Calculating LOP as full gross_salary")
        logger.info(f"present_days = 0, calculating LOP as full gross_salary")
        lop_amount = actual_gross_salary
        lop_amount = lop_amount.quantize(Decimal('0.01'))
        print(f"LOP = actual_gross_salary = {lop_amount}")
        logger.info(f"LOP = actual_gross_salary = {lop_amount}")
        
        # Update LOP in field_values using the correct field code
        field_values[lop_field_code_to_use] = float(lop_amount)
        
        # Update total_deductions: subtract old LOP if it existed, add new LOP
        if lop_was_calculated:
            print(f"Subtracting existing LOP: {existing_lop_amount}")
            logger.info(f"Subtracting existing LOP: {existing_lop_amount}")
            total_deductions -= existing_lop_amount
        print(f"Adding new LOP: {lop_amount}")
        logger.info(f"Adding new LOP: {lop_amount}")
        total_deductions += lop_amount
        context[lop_field_code_to_use] = float(lop_amount)
        print(f"Total deductions after LOP: {total_deductions}")
        logger.info(f"Total deductions after LOP: {total_deductions}")
    elif absent_days > 0:
        # Employee has some present days but also absent days: Calculate LOP
        print(f"\n--- LOP CALCULATION (absent_days > 0) ---")
        print(f"absent_days: {absent_days}, deduction_per_day: {deduction_per_day}")
        logger.info(f"absent_days > 0 ({absent_days}), calculating LOP")
        if working_days > 0:
            lop_amount = absent_days * Decimal(str(deduction_per_day))
            lop_amount = lop_amount.quantize(Decimal('0.01'))
            print(f"LOP = absent_days * deduction_per_day = {absent_days} * {deduction_per_day} = {lop_amount}")
            logger.info(f"LOP = absent_days * deduction_per_day = {absent_days} * {deduction_per_day} = {lop_amount}")
            
            # Update LOP in field_values using the correct field code
            field_values[lop_field_code_to_use] = float(lop_amount)
            
            # Update total_deductions: subtract old LOP if it existed, add new LOP
            if lop_was_calculated:
                print(f"Subtracting existing LOP: {existing_lop_amount}")
                logger.info(f"Subtracting existing LOP: {existing_lop_amount}")
                total_deductions -= existing_lop_amount
            print(f"Adding new LOP: {lop_amount}")
            logger.info(f"Adding new LOP: {lop_amount}")
            total_deductions += lop_amount
            context[lop_field_code_to_use] = float(lop_amount)
            print(f"Total deductions after LOP: {total_deductions}")
            logger.info(f"Total deductions after LOP: {total_deductions}")
        else:
            # Edge case: working_days is 0 (shouldn't happen, but handle gracefully)
            # If no working days, no LOP deduction
            print(f"WARNING: working_days is 0, skipping LOP calculation")
            logger.warning(f"working_days is 0, skipping LOP calculation")
            pass
    else:
        print(f"\n--- LOP CALCULATION ---")
        print(f"No LOP calculation needed (present_days > 0 and absent_days = 0)")
        logger.info(f"No LOP calculation needed (present_days > 0 and absent_days = 0)")
    
    # Calculate net pay
    net_pay = total_earnings - total_deductions
    
    print(f"\n{'='*60}")
    print(f"PAYSLIP CALCULATION SUMMARY")
    print(f"Employee: {employee.name} (ID: {employee.id})")
    print(f"Month: {month}")
    print(f"\n--- SOURCE VALUES ---")
    print(f"  employee.gross_salary: {employee.gross_salary}")
    print(f"  employee.base_salary: {employee.base_salary}")
    print(f"  FINAL gross_salary used: {gross_salary}")
    print(f"\n--- ATTENDANCE ---")
    print(f"  present_days: {attendance['present_days']}")
    print(f"  absent_days: {attendance['absent_days']}")
    print(f"  paid_leave_days: {attendance['paid_leave_days']}")
    print(f"  working_days: {attendance['working_days']}")
    print(f"  net_payable_days: {net_payable_days}")
    print(f"\n--- CALCULATED VALUES ---")
    print(f"  actual_gross_salary (sum of earnings): {actual_gross_salary}")
    print(f"  total_earnings: {total_earnings}")
    print(f"  total_deductions: {total_deductions}")
    print(f"  net_pay: {net_pay}")
    print(f"\n--- FIELD VALUES IN PAYSLIP ---")
    for field_code, value in sorted(field_values.items()):
        print(f"  {field_code}: {value}")
    print(f"{'='*60}\n")
    
    logger.info(f"=== PAYSLIP CALCULATION SUMMARY ===")
    logger.info(f"Employee: {employee.name} (ID: {employee.id})")
    logger.info(f"Month: {month}")
    logger.info(f"--- SOURCE VALUES ---")
    logger.info(f"  employee.gross_salary: {employee.gross_salary}")
    logger.info(f"  employee.base_salary: {employee.base_salary}")
    logger.info(f"  FINAL gross_salary used: {gross_salary}")
    logger.info(f"--- ATTENDANCE ---")
    logger.info(f"  present_days: {attendance['present_days']}")
    logger.info(f"  absent_days: {attendance['absent_days']}")
    logger.info(f"  paid_leave_days: {attendance['paid_leave_days']}")
    logger.info(f"  working_days: {attendance['working_days']}")
    logger.info(f"  net_payable_days: {net_payable_days}")
    logger.info(f"--- CALCULATED VALUES ---")
    logger.info(f"  actual_gross_salary (sum of earnings): {actual_gross_salary}")
    logger.info(f"  total_earnings: {total_earnings}")
    logger.info(f"  total_deductions: {total_deductions}")
    logger.info(f"  net_pay: {net_pay}")
    logger.info(f"--- FIELD VALUES IN PAYSLIP ---")
    for field_code, value in sorted(field_values.items()):
        logger.info(f"  {field_code}: {value}")
    logger.info(f"=== PAYSLIP CALCULATION END ===")
    
    return {
        'field_values': field_values,
        'total_earnings': total_earnings,
        'total_deductions': total_deductions,
        'attendance': attendance,
        'gross_salary': actual_gross_salary  # Return the actual gross (sum of earnings)
    }

