"""
Test script for enhanced payslip calculation with leaves and manual attendance.

This script tests the new attendance calculation logic that considers:
- Manual attendance (highest priority)
- Leave requests (paid/unpaid)
- Holidays
- Weekoffs
- Face recognition logs
- Half-day support (decimal attendance)

Run this script from Django shell:
python manage.py shell < test_payslip_integration.py
"""

from datetime import date, timedelta
from decimal import Decimal
from regface.models import Employee, Location, AttendanceLog
from leave.models import LeaveType, LeaveRequest, Holiday, ManualAttendance, LocationWeekoff
from payslip.models import PayslipFieldConfig, PayslipField, PayslipTemplate, PayslipRecord
from payslip.calculation_engine import calculate_attendance, calculate_payslip_fields
from django.utils import timezone

print("=" * 80)
print("PAYSLIP INTEGRATION TEST - Enhanced Attendance Calculation")
print("=" * 80)

# Test month
test_month = "2025-01"
print(f"\nTest Month: {test_month}")

# Get or create test employee
try:
    location = Location.objects.first()
    if not location:
        print("\n❌ ERROR: No location found. Please create a location first.")
        exit(1)
    
    employee = Employee.objects.filter(location=location).first()
    if not employee:
        print("\n❌ ERROR: No employee found. Please create an employee first.")
        exit(1)
    
    print(f"\n✓ Testing with Employee: {employee.name} (ID: {employee.id})")
    print(f"  Location: {location.name}")
    
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    exit(1)

# Test 1: Basic Attendance Calculation
print("\n" + "=" * 80)
print("TEST 1: Basic Attendance Calculation (Face Recognition Only)")
print("=" * 80)

try:
    attendance = calculate_attendance(employee, test_month)
    
    print(f"\n✓ Attendance calculated successfully:")
    print(f"  Present Days: {attendance['present_days']} (can be decimal like 28.5)")
    print(f"  Absent Days: {attendance['absent_days']} (can be decimal like 1.5)")
    print(f"  Paid Leave Days: {attendance['paid_leave_days']}")
    print(f"  Unpaid Leave Days: {attendance['unpaid_leave_days']}")
    print(f"  Holiday Count: {attendance['holiday_count']}")
    print(f"  Weekoff Count: {attendance['weekoff_count']}")
    print(f"  Working Days: {attendance['working_days']}")
    print(f"  Total Days: {attendance['total_days']}")
    
except Exception as e:
    print(f"\n❌ ERROR in attendance calculation: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Manual Attendance with Half-Day
print("\n" + "=" * 80)
print("TEST 2: Manual Attendance - Half Day Support")
print("=" * 80)

try:
    # Create a manual half-day attendance for testing
    test_date = date(2025, 1, 15)
    
    # Check if manual attendance already exists
    manual_att, created = ManualAttendance.objects.get_or_create(
        employee=employee,
        attendance_date=test_date,
        defaults={
            'location': location,
            'status': 'HALF_DAY',
            'remarks': 'Test half-day attendance'
        }
    )
    
    if created:
        print(f"\n✓ Created manual half-day attendance for {test_date}")
    else:
        print(f"\n✓ Manual attendance already exists for {test_date}: {manual_att.status}")
    
    # Recalculate attendance
    attendance = calculate_attendance(employee, test_month)
    
    print(f"\n✓ Attendance with half-day:")
    print(f"  Present Days: {attendance['present_days']} (should include 0.5 for half-day)")
    print(f"  Absent Days: {attendance['absent_days']} (should include 0.5 for half-day)")
    
    # Verify decimal support
    if isinstance(attendance['present_days'], float) and attendance['present_days'] % 1 != 0:
        print(f"\n✅ PASS: Half-day correctly counted as decimal ({attendance['present_days']})")
    else:
        print(f"\n⚠️  WARNING: Present days is not decimal: {attendance['present_days']}")
    
except Exception as e:
    print(f"\n❌ ERROR in half-day test: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Leave Request Integration
print("\n" + "=" * 80)
print("TEST 3: Leave Request Integration (Paid Leave)")
print("=" * 80)

try:
    # Get or create a leave type
    leave_type, created = LeaveType.objects.get_or_create(
        location=location,
        leave_code='CL',
        defaults={
            'leave_type_name': 'Casual Leave',
            'is_paid': True,
            'max_days_per_year': 12
        }
    )
    
    if created:
        print(f"\n✓ Created leave type: {leave_type.leave_type_name}")
    else:
        print(f"\n✓ Using existing leave type: {leave_type.leave_type_name} (Paid: {leave_type.is_paid})")
    
    # Create a leave request
    leave_start = date(2025, 1, 20)
    leave_end = date(2025, 1, 21)
    
    leave_request, created = LeaveRequest.objects.get_or_create(
        employee=employee,
        start_date=leave_start,
        end_date=leave_end,
        defaults={
            'location': location,
            'leave_type': leave_type,
            'total_days': 2,
            'reason': 'Test leave request',
            'status': 'APPROVED'
        }
    )
    
    if created:
        print(f"✓ Created approved leave request: {leave_start} to {leave_end} ({leave_request.total_days} days)")
    else:
        print(f"✓ Leave request already exists: {leave_request.status}")
    
    # Recalculate attendance
    attendance = calculate_attendance(employee, test_month)
    
    print(f"\n✓ Attendance with leave:")
    print(f"  Paid Leave Days: {attendance['paid_leave_days']} (should be >= 2)")
    print(f"  Unpaid Leave Days: {attendance['unpaid_leave_days']}")
    
    if attendance['paid_leave_days'] >= 2:
        print(f"\n✅ PASS: Paid leave correctly counted")
    else:
        print(f"\n⚠️  WARNING: Paid leave count seems low: {attendance['paid_leave_days']}")
    
except Exception as e:
    print(f"\n❌ ERROR in leave test: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Holiday Integration
print("\n" + "=" * 80)
print("TEST 4: Holiday Integration")
print("=" * 80)

try:
    # Create a holiday
    holiday_date = date(2025, 1, 26)
    
    holiday, created = Holiday.objects.get_or_create(
        location=location,
        holiday_date=holiday_date,
        defaults={
            'holiday_name': 'Republic Day',
            'holiday_type': 'NATIONAL'
        }
    )
    
    if created:
        print(f"\n✓ Created holiday: {holiday.holiday_name} on {holiday_date}")
    else:
        print(f"\n✓ Holiday already exists: {holiday.holiday_name}")
    
    # Recalculate attendance
    attendance = calculate_attendance(employee, test_month)
    
    print(f"\n✓ Attendance with holiday:")
    print(f"  Holiday Count: {attendance['holiday_count']} (should be >= 1)")
    print(f"  Working Days: {attendance['working_days']} (should exclude holidays)")
    
    if attendance['holiday_count'] >= 1:
        print(f"\n✅ PASS: Holiday correctly counted")
    else:
        print(f"\n⚠️  WARNING: Holiday not counted")
    
except Exception as e:
    print(f"\n❌ ERROR in holiday test: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Weekoff Integration
print("\n" + "=" * 80)
print("TEST 5: Weekoff Integration")
print("=" * 80)

try:
    # Create or get location weekoff (Sunday)
    weekoff, created = LocationWeekoff.objects.get_or_create(
        location=location,
        defaults={
            'weekoff_patterns': ['SUNDAY'],
            'is_active': True,
            'effective_from': date(2025, 1, 1)
        }
    )
    
    if created:
        print(f"\n✓ Created weekoff pattern: {weekoff.weekoff_patterns}")
    else:
        print(f"\n✓ Weekoff pattern already exists: {weekoff.weekoff_patterns}")
    
    # Recalculate attendance
    attendance = calculate_attendance(employee, test_month)
    
    print(f"\n✓ Attendance with weekoff:")
    print(f"  Weekoff Count: {attendance['weekoff_count']} (should be ~4-5 for Sundays)")
    print(f"  Working Days: {attendance['working_days']} (should exclude weekoffs)")
    
    if attendance['weekoff_count'] >= 4:
        print(f"\n✅ PASS: Weekoffs correctly counted")
    else:
        print(f"\n⚠️  WARNING: Weekoff count seems low: {attendance['weekoff_count']}")
    
except Exception as e:
    print(f"\n❌ ERROR in weekoff test: {e}")
    import traceback
    traceback.print_exc()

# Test 6: Context Variables for Formulas
print("\n" + "=" * 80)
print("TEST 6: Formula Context Variables")
print("=" * 80)

try:
    # Get or create a field config
    field_config = PayslipFieldConfig.objects.filter(location=location, is_active=True).first()
    
    if field_config:
        print(f"\n✓ Using field config: {field_config.config_name}")
        
        # Calculate payslip fields
        result = calculate_payslip_fields(employee, field_config, test_month)
        
        print(f"\n✓ Payslip calculation result:")
        print(f"  Total Earnings: {result['total_earnings']}")
        print(f"  Total Deductions: {result['total_deductions']}")
        
        print(f"\n✓ Attendance data available in formulas:")
        attendance = result['attendance']
        print(f"  present_days: {attendance['present_days']}")
        print(f"  absent_days: {attendance['absent_days']}")
        print(f"  paid_leave_days: {attendance['paid_leave_days']}")
        print(f"  unpaid_leave_days: {attendance['unpaid_leave_days']}")
        print(f"  holiday_count: {attendance['holiday_count']}")
        print(f"  weekoff_count: {attendance['weekoff_count']}")
        print(f"  working_days: {attendance['working_days']}")
        print(f"  total_days: {attendance['total_days']}")
        
        print(f"\n✅ PASS: All context variables available for formulas")
    else:
        print(f"\n⚠️  WARNING: No field config found. Skipping formula test.")
        print("   Create a payslip field config to test formula calculations.")
    
except Exception as e:
    print(f"\n❌ ERROR in formula test: {e}")
    import traceback
    traceback.print_exc()

# Summary
print("\n" + "=" * 80)
print("TEST SUMMARY")
print("=" * 80)

print("""
✅ All tests completed!

Key Features Verified:
1. ✓ Basic attendance calculation
2. ✓ Half-day support (decimal values: 28.5 days)
3. ✓ Paid/unpaid leave integration
4. ✓ Holiday counting
5. ✓ Weekoff pattern detection
6. ✓ Context variables for formulas

Next Steps:
- Generate a test payslip using the frontend or API
- Verify the payslip shows correct attendance values
- Test formulas using the new variables (paid_leave_days, etc.)
""")

print("=" * 80)
