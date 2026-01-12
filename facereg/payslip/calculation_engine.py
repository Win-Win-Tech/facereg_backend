"""
Calculation Engine for Payslip Field Values

This module handles the calculation of payslip field values based on:
- Employee data (gross_salary, absent_days, etc.)
- Field configurations (PERCENTAGE, FIXED, CALCULATION)
- Previously calculated fields (by field_code)
"""

import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from calendar import monthrange
from django.utils import timezone
import pytz

from regface.models import Employee, AttendanceLog


class CalculationError(Exception):
    """Custom exception for calculation errors"""
    pass


def calculate_attendance(employee, month):
    """
    Enhanced attendance calculation considering:
    - Manual Attendance (highest priority)
    - Leave Requests (approved)
    - Holidays
    - Weekoffs
    - Face Recognition Logs
    
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
            # Get all attendance logs (both checkin and checkout)
            all_logs = AttendanceLog.objects.filter(
                employee=employee,
                timestamp__date__gte=start_date,
                timestamp__date__lte=end_date
            ).order_by('timestamp')
            
            # Group logs by date
            logs_by_date = {}
            for log in all_logs:
                log_date = log.timestamp.date()
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
            
            # Process each day with duration-based logic
            for current_date in logs_by_date.keys():
                date_logs = logs_by_date[current_date]
                
                # Separate checkins and checkouts
                checkin_logs = sorted([log for log in date_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
                checkout_logs = sorted([log for log in date_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
                
                # Calculate total duration by pairing checkins with checkouts
                total_worked_seconds = 0
                min_pairs = min(len(checkin_logs), len(checkout_logs))
                
                for i in range(min_pairs):
                    checkin_log = checkin_logs[i]
                    checkout_log = checkout_logs[i]
                    
                    if checkout_log.timestamp > checkin_log.timestamp:
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
                if not checkin_logs:
                    # No checkin = absent
                    absent_days += Decimal('1')
                elif not checkout_logs:
                    # Checkin but no checkout = absent
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
        all_attendance_logs = AttendanceLog.objects.filter(
            employee=employee,
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date
        ).order_by('timestamp')
        
        # Group logs by date for duration calculation
        logs_by_date = {}
        for log in all_attendance_logs:
            log_date = log.timestamp.date()
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
            
            # Priority 5: Face Recognition Log (with duration-based calculation)
            elif current_date in logs_by_date:
                date_logs = logs_by_date[current_date]
                
                # Separate checkins and checkouts
                checkin_logs = sorted([log for log in date_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
                checkout_logs = sorted([log for log in date_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
                
                # Calculate total duration by pairing checkins with checkouts
                total_worked_seconds = 0
                min_pairs = min(len(checkin_logs), len(checkout_logs))
                
                for i in range(min_pairs):
                    checkin_log = checkin_logs[i]
                    checkout_log = checkout_logs[i]
                    
                    if checkout_log.timestamp > checkin_log.timestamp:
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
                if not checkin_logs:
                    # No checkin = absent
                    absent_days += Decimal('1')
                elif not checkout_logs:
                    # Checkin but no checkout = absent
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
    
    Args:
        formula: String formula (e.g., "BASIC * 0.40", "absent_days * deduction_per_day")
        context: Dictionary with variable values (e.g., {'BASIC': 30000, 'absent_days': 3})
    
    Returns:
        Decimal: Calculated result
    """
    if not formula or not formula.strip():
        raise CalculationError("Empty formula")
    
    formula = formula.strip()
    
    # Replace variable names with their values from context
    # First, sort by length (longest first) to avoid partial replacements
    sorted_keys = sorted(context.keys(), key=len, reverse=True)
    
    for key in sorted_keys:
        if key in context:
            value = context[key]
            # Use word boundaries to ensure exact matches
            pattern = r'\b' + re.escape(str(key)) + r'\b'
            formula = re.sub(pattern, str(float(value)), formula)
    
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


def calculate_field_value(field, context):
    """
    Calculate the value for a single payslip field.
    
    Args:
        field: PayslipField instance
        context: Dictionary with available variables for calculation
    
    Returns:
        Decimal: Calculated field value
    """
    value_type = field.value_type
    value_str = field.value
    
    try:
        if value_type == 'PERCENTAGE':
            # Percentage of gross_salary
            percentage = Decimal(value_str)
            if 'gross_salary' not in context:
                raise CalculationError("gross_salary not found in context for percentage calculation")
            gross = Decimal(str(context['gross_salary']))
            return (gross * percentage / 100).quantize(Decimal('0.01'))
        
        elif value_type == 'FIXED':
            # Fixed amount
            return Decimal(value_str).quantize(Decimal('0.01'))
        
        elif value_type == 'CALCULATION':
            # Formula-based calculation
            return evaluate_formula(value_str, context)
        
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
    attendance = calculate_attendance(employee, month)
    
    # Get gross salary
    gross_salary = employee.gross_salary or employee.base_salary or Decimal('0')
    
    # Calculate deduction_per_day based on working days (not total days)
    # Formula: gross_salary / working_days (excludes holidays and weekoffs)
    working_days = attendance['working_days']
    if working_days > 0:
        deduction_per_day = float(gross_salary) / working_days
    else:
        deduction_per_day = 0.0
    
    # Build initial context with employee data
    context = {
        'gross_salary': float(gross_salary),
        'present_days': attendance['present_days'],  # Can be decimal (28.5)
        'absent_days': attendance['absent_days'],    # Can be decimal (1.5)
        'paid_leave_days': attendance['paid_leave_days'],
        'unpaid_leave_days': attendance['unpaid_leave_days'],
        'holiday_count': attendance['holiday_count'],
        'weekoff_count': attendance['weekoff_count'],
        'working_days': attendance['working_days'],
        'total_days': attendance['total_days'],
        'deduction_per_day': deduction_per_day,  # Auto-calculated: gross_salary / working_days
        'month': month,
        'base_salary': float(employee.base_salary or Decimal('0')),
    }
    
    # Get all fields ordered by display_order
    fields = field_config.fields.filter(is_deleted=False).order_by('display_order')
    
    field_values = {}
    total_earnings = Decimal('0')
    total_deductions = Decimal('0')
    
    # Calculate fields in order (so later fields can reference earlier ones)
    for field in fields:
        if not field.is_visible:
            continue
        
        try:
            value = calculate_field_value(field, context)
            
            # Store the calculated value
            field_values[field.field_code] = float(value)
            
            # Add to context for subsequent calculations
            context[field.field_code] = float(value)
            
            # Update totals
            if field.field_type == 'EARNING':
                total_earnings += value
            elif field.field_type == 'DEDUCTION':
                total_deductions += value
        
        except CalculationError as e:
            # Log error but continue with other fields
            # Store 0 as default or raise if needed
            field_values[field.field_code] = 0.0
            context[field.field_code] = 0.0
            # In production, you might want to log this error
    
    return {
        'field_values': field_values,
        'total_earnings': total_earnings,
        'total_deductions': total_deductions,
        'attendance': attendance
    }

