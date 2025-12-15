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

from regface.models import Employee, AttendanceLog


class CalculationError(Exception):
    """Custom exception for calculation errors"""
    pass


def calculate_attendance(employee, month):
    """
    Calculate present_days, absent_days, and working_days for an employee in a month.
    
    Args:
        employee: Employee instance
        month: Month string in format 'YYYY-MM'
    
    Returns:
        dict: {'present_days': int, 'absent_days': int, 'working_days': int}
    """
    try:
        year, month_num = map(int, month.split('-'))
        # Get first and last day of the month
        start_date = datetime(year, month_num, 1).date()
        _, last_day = monthrange(year, month_num)
        end_date = datetime(year, month_num, last_day).date()
        
        # Get all attendance logs for the employee in this month
        logs = AttendanceLog.objects.filter(
            employee=employee,
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date,
            type=AttendanceLog.CHECKIN
        )
        
        # Create a set of unique dates with check-ins
        present_dates = set()
        for log in logs:
            present_dates.add(log.timestamp.date())
        
        # Calculate present and absent days
        present_days = len(present_dates)
        working_days = (end_date - start_date).days + 1
        absent_days = working_days - present_days
        
        return {
            'present_days': present_days,
            'absent_days': absent_days,
            'working_days': working_days
        }
    except (ValueError, AttributeError) as e:
        # If calculation fails, return defaults
        return {
            'present_days': 0,
            'absent_days': 0,
            'working_days': 30  # Default to 30 days
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
    
    # Build initial context with employee data
    context = {
        'gross_salary': float(gross_salary),
        'present_days': attendance['present_days'],
        'absent_days': attendance['absent_days'],
        'working_days': attendance['working_days'],
        'deduction_per_day': float(employee.deduction_per_day or Decimal('0')),
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

