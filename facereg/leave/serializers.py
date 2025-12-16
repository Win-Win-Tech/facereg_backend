from datetime import datetime
from rest_framework import serializers
from .models import (
    LeaveType, Holiday, LocationWeekoff, EmployeeWeekoff,
    LeaveRequest, LeaveBalance, ManualAttendance
)
from regface.models import Employee, Location, User


class LeaveTypeSerializer(serializers.ModelSerializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.all(), write_only=True
    )
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = LeaveType
        fields = [
            'id', 'location_id', 'location_name', 'leave_type_name', 'leave_code',
            'is_paid', 'max_days_per_year', 'carry_forward_allowed', 'description',
            'is_active', 'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class HolidaySerializer(serializers.ModelSerializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.all(), write_only=True
    )
    location_name = serializers.CharField(source='location.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = Holiday
        fields = [
            'id', 'location_id', 'location_name', 'holiday_name', 'holiday_date',
            'holiday_type', 'is_recurring', 'description', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class HolidayBulkSerializer(serializers.Serializer):
    """Bulk create holidays - accepts array of holiday data"""
    holidays = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )


class HolidayGroupedDatesSerializer(serializers.Serializer):
    """
    Bulk create holidays using grouped dates format.
    
    Format 1 (Simple - auto-generates names):
    {
        "location_id": "uuid",
        "dates": {
            "NATIONAL": ["2025-01-01", "2025-01-26", "2025-08-15"],
            "LOCAL": ["2025-11-01"],
            "RELIGIOUS": ["2025-10-20", "2025-12-25"],
            "OTHER": ["2025-05-10"]
        }
    }
    
    Format 2 (Advanced - custom names):
    {
        "location_id": "uuid",
        "dates": {
            "NATIONAL": [
                {"date": "2025-01-01", "name": "New Year"},
                {"date": "2025-01-26", "name": "Republic Day"}
            ],
            "LOCAL": ["2025-11-01"],  // Can mix both formats
            "RELIGIOUS": ["2025-10-20"]
        }
    }
    
    Note: holiday_name is NOT required. If not provided, it will be auto-generated.
    """
    location_id = serializers.UUIDField(required=True)
    dates = serializers.DictField(
        required=True,
        allow_empty=False,
        help_text="Dictionary with keys: NATIONAL, LOCAL, RELIGIOUS, OTHER. Each value is an array of date strings (YYYY-MM-DD) or objects with 'date' and optional 'name'"
    )
    
    def validate_dates(self, value):
        """Validate dates structure and format"""
        valid_types = ['NATIONAL', 'LOCAL', 'RELIGIOUS', 'OTHER']
        errors = []
        
        for holiday_type, date_list in value.items():
            if holiday_type not in valid_types:
                errors.append(f"Invalid holiday type: {holiday_type}. Must be one of: {valid_types}")
                continue
            
            if not isinstance(date_list, list):
                errors.append(f"{holiday_type}: Must be an array of dates")
                continue
            
            if not date_list:
                continue  # Empty list is allowed (skip this type)
            
            # Validate each date item (can be string or object)
            for idx, date_item in enumerate(date_list):
                date_str = None
                holiday_name = None
                
                if isinstance(date_item, str):
                    # Simple format: just date string
                    date_str = date_item
                elif isinstance(date_item, dict):
                    # Advanced format: object with date and optional name
                    date_str = date_item.get('date')
                    holiday_name = date_item.get('name')
                else:
                    errors.append(f"{holiday_type}[{idx}]: Must be a date string (YYYY-MM-DD) or object with 'date' field")
                    continue
                
                if not date_str:
                    errors.append(f"{holiday_type}[{idx}]: 'date' field is required")
                    continue
                
                if not isinstance(date_str, str):
                    errors.append(f"{holiday_type}[{idx}]: Date must be a string in YYYY-MM-DD format")
                    continue
                
                try:
                    datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    errors.append(f"{holiday_type}[{idx}]: Invalid date format '{date_str}'. Use YYYY-MM-DD")
        
        if errors:
            raise serializers.ValidationError(errors)
        
        return value


class LocationWeekoffSerializer(serializers.ModelSerializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.all(), write_only=True
    )
    location_name = serializers.CharField(source='location.name', read_only=True)
    weekoff_patterns = serializers.ListField(
        child=serializers.ChoiceField(choices=LocationWeekoff.WEEKOFF_PATTERNS),
        required=False,
        allow_empty=True,
        help_text="Array of selected patterns. Examples: ['SUNDAY'], ['SUNDAY', 'ODD_SATURDAY'], ['MONDAY', 'FRIDAY']"
    )

    class Meta:
        model = LocationWeekoff
        fields = [
            'id', 'location_id', 'location_name', 'weekoff_patterns',
            'is_active', 'effective_from', 'effective_to', 'created_by',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_weekoff_patterns(self, value):
        """Ensure patterns list is valid and has no duplicates"""
        if not isinstance(value, list):
            raise serializers.ValidationError("weekoff_patterns must be a list")
        
        valid_patterns = [choice[0] for choice in LocationWeekoff.WEEKOFF_PATTERNS]
        
        # Check for duplicates
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Duplicate patterns are not allowed")
        
        # Validate each pattern
        for pattern in value:
            if pattern not in valid_patterns:
                raise serializers.ValidationError(
                    f"Invalid pattern: {pattern}. Valid options: {valid_patterns}"
                )
        
        # Prevent conflicting patterns for same day (e.g., SUNDAY + ODD_SUNDAY + EVEN_SUNDAY)
        day_base_patterns = {}
        for pattern in value:
            # Extract base day name
            base_day = None
            if pattern in ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']:
                base_day = pattern
            elif pattern.startswith('ODD_'):
                base_day = pattern.replace('ODD_', '')
            elif pattern.startswith('EVEN_'):
                base_day = pattern.replace('EVEN_', '')
            
            if base_day:
                if base_day not in day_base_patterns:
                    day_base_patterns[base_day] = []
                day_base_patterns[base_day].append(pattern)
        
        # Check for conflicts (can't have both SUNDAY + ODD_SUNDAY, or ODD_SUNDAY + EVEN_SUNDAY, etc.)
        for base_day, patterns_list in day_base_patterns.items():
            if len(patterns_list) > 1:
                # Check if any is the "every" pattern
                every_pattern = base_day
                if every_pattern in patterns_list and len(patterns_list) > 1:
                    raise serializers.ValidationError(
                        f"Cannot combine '{every_pattern}' with odd/even patterns for the same day. "
                        f"If you select '{every_pattern}', it already covers all occurrences."
                    )
        
        return value


class EmployeeWeekoffSerializer(serializers.ModelSerializer):
    employee_id = serializers.PrimaryKeyRelatedField(
        source='employee', queryset=Employee.objects.all(), write_only=True
    )
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.all(), write_only=True
    )
    location_name = serializers.CharField(source='location.name', read_only=True)
    weekoff_patterns = serializers.ListField(
        child=serializers.ChoiceField(choices=EmployeeWeekoff.WEEKOFF_PATTERNS),
        required=False,
        allow_empty=True,
        help_text="Array of selected patterns. Examples: ['SUNDAY'], ['SUNDAY', 'ODD_SATURDAY'], ['MONDAY', 'FRIDAY']"
    )
    created_by_name = serializers.CharField(source='created_by.name', read_only=True)

    class Meta:
        model = EmployeeWeekoff
        fields = [
            'id', 'employee_id', 'employee_name', 'location_id', 'location_name',
            'weekoff_patterns',
            'is_active', 'effective_from', 'effective_to', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_weekoff_patterns(self, value):
        """Ensure patterns list is valid and has no duplicates"""
        if not isinstance(value, list):
            raise serializers.ValidationError("weekoff_patterns must be a list")
        
        valid_patterns = [choice[0] for choice in EmployeeWeekoff.WEEKOFF_PATTERNS]
        
        # Check for duplicates
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Duplicate patterns are not allowed")
        
        # Validate each pattern
        for pattern in value:
            if pattern not in valid_patterns:
                raise serializers.ValidationError(
                    f"Invalid pattern: {pattern}. Valid options: {valid_patterns}"
                )
        
        # Prevent conflicting patterns for same day (same logic as LocationWeekoff)
        day_base_patterns = {}
        for pattern in value:
            # Extract base day name
            base_day = None
            if pattern in ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']:
                base_day = pattern
            elif pattern.startswith('ODD_'):
                base_day = pattern.replace('ODD_', '')
            elif pattern.startswith('EVEN_'):
                base_day = pattern.replace('EVEN_', '')
            
            if base_day:
                if base_day not in day_base_patterns:
                    day_base_patterns[base_day] = []
                day_base_patterns[base_day].append(pattern)
        
        # Check for conflicts
        for base_day, patterns_list in day_base_patterns.items():
            if len(patterns_list) > 1:
                every_pattern = base_day
                if every_pattern in patterns_list and len(patterns_list) > 1:
                    raise serializers.ValidationError(
                        f"Cannot combine '{every_pattern}' with odd/even patterns for the same day. "
                        f"If you select '{every_pattern}', it already covers all occurrences."
                    )
        
        return value


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_id = serializers.PrimaryKeyRelatedField(
        source='employee', queryset=Employee.objects.all(), write_only=True
    )
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.all(), write_only=True
    )
    location_name = serializers.CharField(source='location.name', read_only=True)
    leave_type_id = serializers.PrimaryKeyRelatedField(
        source='leave_type', queryset=LeaveType.objects.filter(is_active=True), write_only=True
    )
    leave_type_name = serializers.CharField(source='leave_type.leave_type_name', read_only=True)
    applied_by_name = serializers.CharField(source='applied_by.name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.name', read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            'id', 'employee_id', 'employee_name', 'location_id', 'location_name',
            'leave_type_id', 'leave_type_name', 'start_date', 'end_date', 'total_days',
            'reason', 'status', 'approved_by', 'approved_by_name', 'approved_on',
            'rejection_reason', 'applied_by', 'applied_by_name', 'applied_on', 'is_active'
        ]
        read_only_fields = [
            'id', 'approved_by', 'approved_on', 'rejection_reason',
            'applied_on', 'total_days'
        ]


class LeaveBalanceSerializer(serializers.ModelSerializer):
    employee_id = serializers.PrimaryKeyRelatedField(
        source='employee', queryset=Employee.objects.all(), write_only=True
    )
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.all(), write_only=True
    )
    leave_type_id = serializers.PrimaryKeyRelatedField(
        source='leave_type', queryset=LeaveType.objects.filter(is_active=True), write_only=True
    )
    leave_type_name = serializers.CharField(source='leave_type.leave_type_name', read_only=True)
    leave_code = serializers.CharField(source='leave_type.leave_code', read_only=True)

    class Meta:
        model = LeaveBalance
        fields = [
            'id', 'employee_id', 'employee_name', 'location_id', 'leave_type_id',
            'leave_type_name', 'leave_code', 'year', 'total_allocated', 'used_days',
            'pending_days', 'carry_forward_from_previous', 'available_days', 'updated_at'
        ]
        read_only_fields = ['id', 'updated_at']


class ManualAttendanceSerializer(serializers.ModelSerializer):
    employee_id = serializers.PrimaryKeyRelatedField(
        source='employee', queryset=Employee.objects.all(), write_only=True
    )
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    location_id = serializers.PrimaryKeyRelatedField(
        source='location', queryset=Location.objects.all(), write_only=True
    )
    location_name = serializers.CharField(source='location.name', read_only=True)
    leave_request_id = serializers.PrimaryKeyRelatedField(
        source='leave_request', queryset=LeaveRequest.objects.filter(status='APPROVED'),
        write_only=True, required=False, allow_null=True
    )
    marked_by_name = serializers.CharField(source='marked_by.name', read_only=True)

    class Meta:
        model = ManualAttendance
        fields = [
            'id', 'employee_id', 'employee_name', 'location_id', 'location_name',
            'attendance_date', 'status', 'leave_request_id', 'check_in_time',
            'check_out_time', 'remarks', 'marked_by', 'marked_by_name', 'marked_on',
            'is_override'
        ]
        read_only_fields = ['id', 'marked_on']


class ManualAttendanceBulkCreateSerializer(serializers.Serializer):
    """
    Bulk create manual attendance records for multiple employees with different dates.
    
    Format:
    {
        "attendances": [
            {
                "employee_id": "uuid",
                "dates": [
                    {"date": "2025-01-15", "status": "PRESENT", "check_in_time": "09:00", "remarks": "..."},
                    {"date": "2025-01-16", "status": "ABSENT", "remarks": "..."}
                ]
            },
            {
                "employee_id": "uuid",
                "dates": [
                    {"date": "2025-01-20", "status": "PRESENT"},
                    {"date": "2025-01-21", "status": "HALF_DAY"},
                    {"date": "2025-01-22", "status": "PRESENT"}
                ]
            }
        ]
    }
    """
    attendances = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )

    def validate_attendances(self, value):
        """Validate each attendance entry"""
        errors = []
        for idx, attendance in enumerate(value):
            if 'employee_id' not in attendance:
                errors.append(f"attendances[{idx}]: 'employee_id' is required")
            if 'dates' not in attendance or not attendance['dates']:
                errors.append(f"attendances[{idx}]: 'dates' is required and cannot be empty")
        
        if errors:
            raise serializers.ValidationError(errors)
        return value


class ManualAttendanceBulkUpdateSerializer(serializers.Serializer):
    """Bulk update manual attendance - requires id for each record"""
    attendances = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )


class ManualAttendanceBulkDeleteSerializer(serializers.Serializer):
    """Bulk delete manual attendance records"""
    attendance_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        min_length=1
    )


class LeaveRequestBulkCreateSerializer(serializers.Serializer):
    """
    Bulk create leave requests for multiple employees with different dates and leave types.
    
    Format:
    {
        "leave_requests": [
            {
                "employee_id": "uuid",
                "leave_type_id": "uuid",
                "start_date": "2025-01-15",
                "end_date": "2025-01-17",
                "reason": "Sick leave"
            },
            {
                "employee_id": "uuid",
                "leave_type_id": "uuid",
                "start_date": "2025-01-20",
                "end_date": "2025-01-20",
                "reason": "Personal work"
            },
            {
                "employee_id": "uuid-2",
                "leave_type_id": "uuid-2",
                "start_date": "2025-01-25",
                "end_date": "2025-01-27",
                "reason": "Family function"
            }
        ]
    }
    
    Note: When admin creates leave requests, they default to APPROVED status.
    Status can be changed later via update/approve/reject actions.
    """
    leave_requests = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
        min_length=1
    )
    
    def validate_leave_requests(self, value):
        """Validate each leave request entry"""
        errors = []
        required_fields = ['employee_id', 'leave_type_id', 'start_date', 'end_date']
        
        for idx, leave_req in enumerate(value):
            for field in required_fields:
                if field not in leave_req:
                    errors.append(f"leave_requests[{idx}]: '{field}' is required")
        
        if errors:
            raise serializers.ValidationError(errors)
        
        return value


class ManualAttendanceBulkMarkSerializer(serializers.Serializer):
    """
    Advanced bulk mark attendance operations.
    
    Types:
    - "all": Mark all employees for a location
    - "except": Mark all except specified employee IDs
    - "specific": Mark only specified employee IDs
    
    Format examples:
    
    Mark all present:
    {
        "date": "2025-01-15",
        "location_id": "uuid",
        "type": "all",
        "status": "PRESENT",
        "check_in_time": "09:00:00",
        "check_out_time": "18:00:00"
    }
    
    Mark all absent except:
    {
        "date": "2025-01-15",
        "location_id": "uuid",
        "type": "except",
        "status": "ABSENT",
        "except_employee_ids": ["uuid-1", "uuid-2"]
    }
    
    Mark specific employees as weekoff:
    {
        "date": "2025-01-15",
        "location_id": "uuid",
        "type": "specific",
        "status": "WEEKOFF",
        "employee_ids": ["uuid-1", "uuid-2", "uuid-3"]
    }
    """
    date = serializers.DateField(required=True)
    location_id = serializers.UUIDField(required=True)
    type = serializers.ChoiceField(
        choices=[('all', 'All'), ('except', 'Except'), ('specific', 'Specific')],
        required=True
    )
    status = serializers.ChoiceField(
        choices=ManualAttendance.STATUS_CHOICES,
        required=True
    )
    except_employee_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        help_text="Required if type='except'. Employee IDs to exclude."
    )
    employee_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        help_text="Required if type='specific'. Employee IDs to mark."
    )
    check_in_time = serializers.TimeField(required=False, allow_null=True)
    check_out_time = serializers.TimeField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    
    def validate(self, attrs):
        """Validate based on type"""
        mark_type = attrs.get('type')
        except_ids = attrs.get('except_employee_ids', [])
        employee_ids = attrs.get('employee_ids', [])
        
        if mark_type == 'except':
            if not except_ids:
                raise serializers.ValidationError({
                    "except_employee_ids": "This field is required when type='except'"
                })
        
        if mark_type == 'specific':
            if not employee_ids:
                raise serializers.ValidationError({
                    "employee_ids": "This field is required when type='specific'"
                })
        
        return attrs

