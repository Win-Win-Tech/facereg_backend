import logging
from datetime import date, datetime
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from openpyxl import load_workbook
from io import BytesIO

from regface.models import User, Employee, Location
from regface.views import AuthenticatedAPIView, is_superadmin
from rest_framework import permissions
from regface.authentication import SimpleTokenAuthentication
from .models import (
    LeaveType, Holiday, LocationWeekoff, EmployeeWeekoff,
    LeaveRequest, LeaveBalance, ManualAttendance
)
from .serializers import (
    LeaveTypeSerializer,
    HolidaySerializer, HolidayBulkSerializer, HolidayGroupedDatesSerializer,
    LocationWeekoffSerializer,
    EmployeeWeekoffSerializer,
    LeaveRequestSerializer, LeaveRequestBulkCreateSerializer,
    LeaveBalanceSerializer,
    ManualAttendanceSerializer,
    ManualAttendanceBulkCreateSerializer,
    ManualAttendanceBulkUpdateSerializer,
    ManualAttendanceBulkDeleteSerializer,
    ManualAttendanceBulkMarkSerializer,
)

logger = logging.getLogger(__name__)


# ==================== Helper Functions ====================

def get_employee_location(employee_id):
    """Get employee's location"""
    try:
        employee = Employee.objects.get(id=employee_id)
        return employee.location_id
    except Employee.DoesNotExist:
        return None


def check_location_access(request, location_id):
    """Check if user has access to location"""
    if request.user.role == User.Role.ADMIN:
        if not request.user.location_id:
            return False, Response(
                {"detail": "Admin user is not assigned to a location."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.user.location_id != location_id:
            return False, Response(status=status.HTTP_403_FORBIDDEN)
    return True, None


# ==================== LeaveType APIs ====================

class LeaveTypeListCreateView(AuthenticatedAPIView):
    def get(self, request):
        location_id = request.query_params.get('location_id')
        leave_types = LeaveType.objects.filter(is_active=True)
        
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            leave_types = leave_types.filter(location=request.user.location)
        elif location_id:
            leave_types = leave_types.filter(location_id=location_id)
        
        serializer = LeaveTypeSerializer(leave_types, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = LeaveTypeSerializer(data=request.data)
        if serializer.is_valid():
            location_id = serializer.validated_data['location'].id
            has_access, error_response = check_location_access(request, location_id)
            if not has_access:
                return error_response
            
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LeaveTypeDetailView(AuthenticatedAPIView):
    def get_object(self, pk):
        try:
            return LeaveType.objects.get(pk=pk, is_active=True)
        except LeaveType.DoesNotExist:
            return None

    def get(self, request, pk):
        leave_type = self.get_object(pk)
        if not leave_type:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        has_access, error_response = check_location_access(request, leave_type.location_id)
        if not has_access:
            return error_response
        
        serializer = LeaveTypeSerializer(leave_type)
        return Response(serializer.data)

    def patch(self, request, pk):
        leave_type = self.get_object(pk)
        if not leave_type:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        has_access, error_response = check_location_access(request, leave_type.location_id)
        if not has_access:
            return error_response
        
        serializer = LeaveTypeSerializer(leave_type, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        leave_type = self.get_object(pk)
        if not leave_type:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        has_access, error_response = check_location_access(request, leave_type.location_id)
        if not has_access:
            return error_response
        
        leave_type.is_active = False
        leave_type.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ==================== Holiday APIs ====================

class HolidayListCreateView(AuthenticatedAPIView):
    def get(self, request):
        location_id = request.query_params.get('location_id')
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        
        holidays = Holiday.objects.all()
        
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            holidays = holidays.filter(location=request.user.location)
        elif location_id:
            holidays = holidays.filter(location_id=location_id)
        
        if year:
            holidays = holidays.filter(holiday_date__year=year)
        if month:
            holidays = holidays.filter(holiday_date__month=month)
        
        serializer = HolidaySerializer(holidays, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = HolidaySerializer(data=request.data)
        if serializer.is_valid():
            location_id = serializer.validated_data['location'].id
            has_access, error_response = check_location_access(request, location_id)
            if not has_access:
                return error_response
            
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class HolidayBulkCreateView(AuthenticatedAPIView):
    """Bulk create holidays"""
    def post(self, request):
        serializer = HolidayBulkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        holidays_data = serializer.validated_data['holidays']
        created_holidays = []
        errors = []
        
        for idx, holiday_data in enumerate(holidays_data):
            try:
                location_id = holiday_data.get('location_id')
                if location_id:
                    has_access, error_response = check_location_access(request, location_id)
                    if not has_access:
                        errors.append({
                            "row": idx + 1,
                            "error": "Access denied to this location"
                        })
                        continue
                
                # Check if holiday already exists (for update)
                holiday_date = holiday_data.get('holiday_date')
                holiday_name = holiday_data.get('holiday_name')
                
                existing_holiday = None
                if location_id and holiday_date and holiday_name:
                    try:
                        location = Location.objects.get(pk=location_id)
                        existing_holiday = Holiday.objects.filter(
                            location=location,
                            holiday_date=holiday_date,
                            holiday_name=holiday_name
                        ).first()
                    except Exception:
                        pass
                
                if existing_holiday:
                    # Update existing holiday
                    holiday_serializer = HolidaySerializer(existing_holiday, data=holiday_data, partial=True)
                    if holiday_serializer.is_valid():
                        holiday = holiday_serializer.save()
                        created_holidays.append(holiday)
                    else:
                        errors.append({
                            "row": idx + 1,
                            "holiday_name": holiday_name,
                            "holiday_date": str(holiday_date),
                            "error": "Failed to update existing holiday",
                            "details": holiday_serializer.errors
                        })
                else:
                    # Create new holiday
                    holiday_serializer = HolidaySerializer(data=holiday_data)
                    if holiday_serializer.is_valid():
                        holiday = holiday_serializer.save(created_by=request.user)
                        created_holidays.append(holiday)
                    else:
                        # Format serializer errors for better readability
                        error_messages = []
                        for field, field_errors in holiday_serializer.errors.items():
                            if isinstance(field_errors, list):
                                error_messages.extend([f"{field}: {err}" for err in field_errors])
                            else:
                                error_messages.append(f"{field}: {field_errors}")
                        
                        errors.append({
                            "row": idx + 1,
                            "holiday_name": holiday_name or "N/A",
                            "error": "; ".join(error_messages) if error_messages else "Validation failed"
                        })
            except Exception as e:
                errors.append({
                    "row": idx + 1,
                    "error": f"Unexpected error: {str(e)}"
                })
        
        if errors and not created_holidays:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        
        result_serializer = HolidaySerializer(created_holidays, many=True)
        response_data = {
            "message": f"Successfully processed {len(created_holidays)} holiday(s)",
            "created": result_serializer.data,
            "count": len(created_holidays)
        }
        if errors:
            response_data["errors"] = errors
            response_data["error_count"] = len(errors)
        
        status_code = status.HTTP_201_CREATED if created_holidays else status.HTTP_400_BAD_REQUEST
        return Response(response_data, status=status_code)


class HolidayDetailView(AuthenticatedAPIView):
    def get_object(self, pk):
        try:
            return Holiday.objects.get(pk=pk)
        except Holiday.DoesNotExist:
            return None

    def get(self, request, pk):
        holiday = self.get_object(pk)
        if not holiday:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        has_access, error_response = check_location_access(request, holiday.location_id)
        if not has_access:
            return error_response
        
        serializer = HolidaySerializer(holiday)
        return Response(serializer.data)

    def patch(self, request, pk):
        holiday = self.get_object(pk)
        if not holiday:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        has_access, error_response = check_location_access(request, holiday.location_id)
        if not has_access:
            return error_response
        
        serializer = HolidaySerializer(holiday, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def put(self, request, pk):
        """PUT method for full update (same as PATCH but requires all fields)"""
        holiday = self.get_object(pk)
        if not holiday:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        has_access, error_response = check_location_access(request, holiday.location_id)
        if not has_access:
            return error_response
        
        serializer = HolidaySerializer(holiday, data=request.data, partial=False)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        holiday = self.get_object(pk)
        if not holiday:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        has_access, error_response = check_location_access(request, holiday.location_id)
        if not has_access:
            return error_response
        
        holiday.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class HolidayGroupedDatesBulkCreateView(AuthenticatedAPIView):
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
    
    Note: holiday_name is NOT required. If not provided, it will be auto-generated as "{Type} Holiday - {date}".
    You can mix both formats in the same request.
    """
    def post(self, request):
        serializer = HolidayGroupedDatesSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        location_id = serializer.validated_data['location_id']
        dates_dict = serializer.validated_data['dates']
        
        # Check location access
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        try:
            location = Location.objects.get(pk=location_id)
        except Location.DoesNotExist:
            return Response(
                {"detail": "Location not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        created_holidays = []
        errors = []
        
        # Map holiday types to default names
        type_names = {
            'NATIONAL': 'National Holiday',
            'LOCAL': 'Local Holiday',
            'RELIGIOUS': 'Religious Holiday',
            'OTHER': 'Holiday'
        }
        
        # Process each holiday type
        for holiday_type, date_list in dates_dict.items():
            if not date_list:  # Skip empty lists
                continue
            
            for date_item in date_list:
                try:
                    # Handle both string and object formats
                    if isinstance(date_item, str):
                        # Simple format: just date string, auto-generate name
                        date_str = date_item
                        holiday_name = None  # Will be auto-generated
                    elif isinstance(date_item, dict):
                        # Advanced format: object with date and optional name
                        date_str = date_item.get('date')
                        holiday_name = date_item.get('name')  # Can be None
                    else:
                        errors.append(f"{holiday_type}: Invalid date format. Must be string or object")
                        continue
                    
                    holiday_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
                    # Use custom name if provided, otherwise auto-generate
                    if not holiday_name:
                        holiday_name = f"{type_names.get(holiday_type, 'Holiday')} - {date_str}"
                    
                    # Check if holiday already exists (for update)
                    existing = Holiday.objects.filter(
                        location=location,
                        holiday_date=holiday_date,
                        holiday_name=holiday_name
                    ).first()
                    
                    if existing:
                        # Update existing holiday
                        existing.holiday_type = holiday_type
                        existing.is_recurring = False
                        existing.save()
                        created_holidays.append(existing)
                    else:
                        # Create new holiday
                        holiday = Holiday.objects.create(
                            location=location,
                            holiday_name=holiday_name,
                            holiday_date=holiday_date,
                            holiday_type=holiday_type,
                            is_recurring=False,
                            created_by=request.user
                        )
                        created_holidays.append(holiday)
                    
                except ValueError as e:
                    date_str = date_item if isinstance(date_item, str) else date_item.get('date', 'unknown')
                    errors.append({
                        "holiday_type": holiday_type,
                        "date": date_str,
                        "error": "Invalid date format. Use YYYY-MM-DD format (e.g., 2025-01-01)"
                    })
                except Exception as e:
                    date_str = date_item if isinstance(date_item, str) else date_item.get('date', 'unknown')
                    errors.append({
                        "holiday_type": holiday_type,
                        "date": date_str,
                        "error": f"Error processing holiday: {str(e)}"
                    })
        
        if errors and not created_holidays:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        
        result_serializer = HolidaySerializer(created_holidays, many=True)
        response_data = {
            "message": f"Successfully processed {len(created_holidays)} holiday(s)",
            "created": result_serializer.data,
            "count": len(created_holidays)
        }
        if errors:
            response_data["errors"] = errors
            response_data["error_count"] = len(errors)
        
        status_code = status.HTTP_201_CREATED if created_holidays else status.HTTP_400_BAD_REQUEST
        return Response(response_data, status=status_code)


class HolidayExcelUploadView(AuthenticatedAPIView):
    """
    Upload Excel file to bulk create holidays.
    
    Expected Excel format:
    - Headers in first row: location_id (or location_name), holiday_date (or date), holiday_name (or name), holiday_type (or type), description (or desc/remarks)
    - Required columns: holiday_date (YYYY-MM-DD or Excel date format), holiday_name, holiday_type
    - Optional columns: location_id, location_name, description
    - Note: is_recurring is NOT processed from Excel (always defaults to False)
    - If location_name is provided, it will be used to find location_id
    """
    def post(self, request):
        excel_file = request.FILES.get('excel_file')
        location_id_param = request.data.get('location_id')  # Optional fallback
        
        if not excel_file:
            return Response(
                {"detail": "Excel file is required. Use 'excel_file' as the field name."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not excel_file.name.endswith(('.xlsx', '.xls')):
            return Response(
                {"detail": "Invalid file format. Only .xlsx and .xls files are supported."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Read Excel file
            wb = load_workbook(filename=BytesIO(excel_file.read()))
            ws = wb.active
            
            # Get headers (first row)
            headers = [cell.value for cell in ws[1]]
            headers = [str(h).strip().lower() if h else '' for h in headers]
            
            # Map header variations to standard names
            header_map = {
                'location_id': ['location_id', 'locationid', 'location uuid'],
                'location_name': ['location_name', 'locationname', 'location'],
                'holiday_date': ['holiday_date', 'holidaydate', 'date', 'holiday date'],
                'holiday_name': ['holiday_name', 'holidayname', 'name', 'holiday name'],
                'holiday_type': ['holiday_type', 'holidaytype', 'type', 'holiday type'],
                'description': ['description', 'desc', 'remarks']
            }
            
            # Find column indices
            col_indices = {}
            for standard_name, variations in header_map.items():
                for idx, header in enumerate(headers):
                    if header in variations:
                        col_indices[standard_name] = idx + 1  # Excel is 1-indexed
                        break
            
            # Validate required columns
            if 'holiday_date' not in col_indices:
                return Response(
                    {"detail": "Required column 'holiday_date' (or 'date') not found in Excel file."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if 'holiday_name' not in col_indices:
                return Response(
                    {"detail": "Required column 'holiday_name' (or 'name') not found in Excel file."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if 'holiday_type' not in col_indices:
                return Response(
                    {"detail": "Required column 'holiday_type' (or 'type') not found in Excel file."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Process rows
            created_holidays = []
            errors = []
            
            for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=False), start=2):
                try:
                    # Get cell values
                    holiday_date_cell = row[col_indices['holiday_date'] - 1]
                    holiday_name_cell = row[col_indices['holiday_name'] - 1]
                    holiday_type_cell = row[col_indices['holiday_type'] - 1]
                    
                    # Skip empty rows
                    if not holiday_date_cell.value or not holiday_name_cell.value:
                        continue
                    
                    # Parse holiday_date
                    holiday_date_value = holiday_date_cell.value
                    if isinstance(holiday_date_value, datetime):
                        holiday_date = holiday_date_value.date()
                    elif isinstance(holiday_date_value, date):
                        holiday_date = holiday_date_value
                    else:
                        holiday_date_str = str(holiday_date_value).strip()
                        holiday_date = datetime.strptime(holiday_date_str, '%Y-%m-%d').date()
                    
                    # Get holiday_name
                    holiday_name = str(holiday_name_cell.value).strip()
                    
                    # Get holiday_type
                    holiday_type = str(holiday_type_cell.value).strip().upper()
                    if holiday_type not in ['NATIONAL', 'LOCAL', 'RELIGIOUS', 'OTHER']:
                        errors.append(f"Row {row_idx}: Invalid holiday_type '{holiday_type}'. Must be one of: NATIONAL, LOCAL, RELIGIOUS, OTHER")
                        continue
                    
                    # Get location_id
                    location_id = None
                    if 'location_id' in col_indices:
                        location_id_value = row[col_indices['location_id'] - 1].value
                        if location_id_value:
                            location_id = str(location_id_value).strip()
                    elif 'location_name' in col_indices:
                        location_name_value = row[col_indices['location_name'] - 1].value
                        if location_name_value:
                            location_name = str(location_name_value).strip()
                            try:
                                location = Location.objects.get(name=location_name)
                                location_id = str(location.id)
                            except Location.DoesNotExist:
                                errors.append(f"Row {row_idx}: Location '{location_name}' not found")
                                continue
                    
                    # Use location_id from parameter if not in Excel
                    if not location_id and location_id_param:
                        location_id = location_id_param
                    
                    if not location_id:
                        errors.append(f"Row {row_idx}: location_id is required (provide in Excel or as parameter)")
                        continue
                    
                    # Check location access
                    has_access, error_response = check_location_access(request, location_id)
                    if not has_access:
                        errors.append(f"Row {row_idx}: Access denied to location")
                        continue
                    
                    try:
                        location = Location.objects.get(pk=location_id)
                    except Location.DoesNotExist:
                        errors.append(f"Row {row_idx}: Location not found")
                        continue
                    
                    # is_recurring is not processed from Excel (always defaults to False)
                    is_recurring = False
                    
                    # Get description (optional)
                    description = None
                    if 'description' in col_indices:
                        desc_value = row[col_indices['description'] - 1].value
                        if desc_value:
                            description = str(desc_value).strip()
                    
                    # Check if holiday already exists (for update)
                    existing = Holiday.objects.filter(
                        location=location,
                        holiday_date=holiday_date,
                        holiday_name=holiday_name
                    ).first()
                    
                    if existing:
                        # Update existing holiday
                        existing.holiday_type = holiday_type
                        existing.description = description
                        existing.is_recurring = is_recurring
                        existing.save()
                        created_holidays.append(existing)
                    else:
                        # Create new holiday
                        holiday = Holiday.objects.create(
                            location=location,
                            holiday_name=holiday_name,
                            holiday_date=holiday_date,
                            holiday_type=holiday_type,
                            is_recurring=is_recurring,
                            description=description,
                            created_by=request.user
                        )
                        created_holidays.append(holiday)
                    
                except ValueError as e:
                    errors.append({
                        "row": row_idx,
                        "error": f"Invalid date format. Use YYYY-MM-DD format",
                        "date_value": str(holiday_date_cell.value) if holiday_date_cell.value else "empty"
                    })
                    continue
                except Exception as e:
                    errors.append({
                        "row": row_idx,
                        "error": f"Error processing row: {str(e)}",
                        "holiday_name": str(holiday_name_cell.value) if holiday_name_cell.value else "N/A"
                    })
                    continue
            
            if errors and not created_holidays:
                return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
            
            result_serializer = HolidaySerializer(created_holidays, many=True)
            response_data = {
                "message": f"Successfully processed {len(created_holidays)} holiday(s) from {ws.max_row - 1} row(s)",
                "created": result_serializer.data,
                "count": len(created_holidays),
                "total_rows_processed": ws.max_row - 1
            }
            if errors:
                response_data["errors"] = errors
                response_data["error_count"] = len(errors)
            
            status_code = status.HTTP_201_CREATED if created_holidays else status.HTTP_400_BAD_REQUEST
            return Response(response_data, status=status_code)
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {str(e)}", exc_info=True)
            return Response(
                {"detail": f"Error processing Excel file: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )


# ==================== LocationWeekoff APIs ====================

class LocationWeekoffViewSet(viewsets.ModelViewSet):
    """
    ViewSet for LocationWeekoff CRUD operations.
    Provides list, create, retrieve, update, partial_update, and destroy.
    """
    queryset = LocationWeekoff.objects.filter(is_active=True)
    serializer_class = LocationWeekoffSerializer
    authentication_classes = [SimpleTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Filter queryset based on user role and location_id query param"""
        queryset = super().get_queryset()
        location_id = self.request.query_params.get('location_id')
        
        if self.request.user.role == User.Role.ADMIN:
            if not self.request.user.location_id:
                return queryset.none()  # Return empty queryset, error handled in list()
            queryset = queryset.filter(location=self.request.user.location)
        elif location_id:
            queryset = queryset.filter(location_id=location_id)
        
        return queryset
    
    def list(self, request, *args, **kwargs):
        """List all location weekoffs with access control"""
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().list(request, *args, **kwargs)
    
    def create(self, request, *args, **kwargs):
        """Create location weekoff, but update if location already has one"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        location_id = serializer.validated_data['location'].id
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        # Check if location already has weekoff
        existing = LocationWeekoff.objects.filter(location_id=location_id, is_active=True).first()
        if existing:
            # Update existing instead of creating new
            update_serializer = LocationWeekoffSerializer(existing, data=request.data, partial=True)
            update_serializer.is_valid(raise_exception=True)
            update_serializer.save()
            return Response(update_serializer.data, status=status.HTTP_200_OK)
        
        serializer.save(created_by=request.user)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    def retrieve(self, request, *args, **kwargs):
        """Retrieve location weekoff with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().retrieve(request, *args, **kwargs)
    
    def update(self, request, *args, **kwargs):
        """Update location weekoff with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().update(request, *args, **kwargs)
    
    def partial_update(self, request, *args, **kwargs):
        """Partial update location weekoff with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().partial_update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        """Soft delete location weekoff (set is_active=False)"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ==================== EmployeeWeekoff APIs ====================

class EmployeeWeekoffViewSet(viewsets.ModelViewSet):
    """
    ViewSet for EmployeeWeekoff CRUD operations.
    Provides list, create, retrieve, update, partial_update, and destroy.
    """
    queryset = EmployeeWeekoff.objects.filter(is_active=True)
    serializer_class = EmployeeWeekoffSerializer
    authentication_classes = [SimpleTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Filter queryset based on user role and query params"""
        queryset = super().get_queryset()
        employee_id = self.request.query_params.get('employee_id')
        location_id = self.request.query_params.get('location_id')
        
        if self.request.user.role == User.Role.ADMIN:
            if not self.request.user.location_id:
                return queryset.none()  # Return empty queryset, error handled in list()
            queryset = queryset.filter(location=self.request.user.location)
        elif location_id:
            queryset = queryset.filter(location_id=location_id)
        
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        
        return queryset
    
    def list(self, request, *args, **kwargs):
        """List all employee weekoffs with access control"""
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().list(request, *args, **kwargs)
    
    def create(self, request, *args, **kwargs):
        """Create employee weekoff, but update if employee already has one"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        location_id = serializer.validated_data['location'].id
        employee_id = serializer.validated_data['employee'].id
        
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        # Verify employee belongs to location
        try:
            employee = Employee.objects.get(pk=employee_id)
            if employee.location_id != location_id:
                return Response(
                    {"detail": "Employee does not belong to the specified location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Employee.DoesNotExist:
            return Response(
                {"detail": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # Check if employee already has weekoff
        existing = EmployeeWeekoff.objects.filter(employee_id=employee_id, is_active=True).first()
        if existing:
            # Update existing instead of creating new
            update_serializer = EmployeeWeekoffSerializer(existing, data=request.data, partial=True)
            update_serializer.is_valid(raise_exception=True)
            update_serializer.save()
            return Response(update_serializer.data, status=status.HTTP_200_OK)
        
        serializer.save(created_by=request.user)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    def retrieve(self, request, *args, **kwargs):
        """Retrieve employee weekoff with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().retrieve(request, *args, **kwargs)
    
    def update(self, request, *args, **kwargs):
        """Update employee weekoff with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().update(request, *args, **kwargs)
    
    def partial_update(self, request, *args, **kwargs):
        """Partial update employee weekoff with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().partial_update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        """Soft delete employee weekoff (set is_active=False)"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ==================== LeaveRequest APIs ====================

class LeaveRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for LeaveRequest CRUD operations.
    Provides list, create, retrieve, update, partial_update, and destroy.
    Also includes custom actions: approve and reject.
    """
    queryset = LeaveRequest.objects.filter(is_active=True)
    serializer_class = LeaveRequestSerializer
    authentication_classes = [SimpleTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Filter queryset based on user role and query params"""
        queryset = super().get_queryset()
        employee_id = self.request.query_params.get('employee_id')
        location_id = self.request.query_params.get('location_id')
        status_filter = self.request.query_params.get('status')
        month = self.request.query_params.get('month')  # YYYY-MM
        
        if self.request.user.role == User.Role.ADMIN:
            if not self.request.user.location_id:
                return queryset.none()  # Return empty queryset, error handled in list()
            queryset = queryset.filter(location=self.request.user.location)
        elif location_id:
            queryset = queryset.filter(location_id=location_id)
        
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if month:
            year, month_num = map(int, month.split('-'))
            queryset = queryset.filter(
                Q(start_date__year=year, start_date__month=month_num) |
                Q(end_date__year=year, end_date__month=month_num) |
                Q(start_date__lte=date(year, month_num, 1), end_date__gte=date(year, month_num, 28))
            )
        
        return queryset
    
    def list(self, request, *args, **kwargs):
        """List all leave requests with access control"""
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().list(request, *args, **kwargs)
    
    def create(self, request, *args, **kwargs):
        """Create leave request"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        employee_id = serializer.validated_data['employee'].id
        location_id = get_employee_location(employee_id)
        if not location_id:
            return Response(
                {"detail": "Employee not found or has no location."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        # Calculate total_days (excluding weekoffs/holidays) - simplified for now
        start_date = serializer.validated_data['start_date']
        end_date = serializer.validated_data['end_date']
        total_days = (end_date - start_date).days + 1
        
        # Admin creates leave requests as APPROVED by default (if status not provided)
        # Status can be specified in request.data or changed later via update
        request_status = serializer.validated_data.get('status', 'APPROVED')
        
        save_kwargs = {
            'applied_by': request.user,
            'total_days': total_days,
            'status': request_status,
        }
        
        # If status is APPROVED, set approved_by and approved_on
        if request_status == 'APPROVED':
            save_kwargs['approved_by'] = request.user
            save_kwargs['approved_on'] = datetime.now()
        
        serializer.save(**save_kwargs)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    def retrieve(self, request, *args, **kwargs):
        """Retrieve leave request with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().retrieve(request, *args, **kwargs)
    
    def update(self, request, *args, **kwargs):
        """Update leave request with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        
        # Allow updates for any status (admin can change status, dates, etc.)
        # Status changes are handled via approve/reject actions or direct update
        return super().update(request, *args, **kwargs)
    
    def partial_update(self, request, *args, **kwargs):
        """Partial update leave request with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        
        # Allow updates for any status (admin can change status, dates, etc.)
        # Status changes are handled via approve/reject actions or direct update
        return super().partial_update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        """Cancel leave request (soft delete)"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        
        instance.is_active = False
        instance.status = 'CANCELLED'
        instance.save(update_fields=['is_active', 'status'])
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve leave request"""
        leave_request = self.get_object()
        has_access, error_response = check_location_access(request, leave_request.location_id)
        if not has_access:
            return error_response
        
        if leave_request.status != 'PENDING':
            return Response(
                {"detail": f"Leave request is already {leave_request.status.lower()}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        leave_request.status = 'APPROVED'
        leave_request.approved_by = request.user
        leave_request.approved_on = datetime.now()
        leave_request.save(update_fields=['status', 'approved_by', 'approved_on'])
        
        serializer = self.get_serializer(leave_request)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject leave request"""
        leave_request = self.get_object()
        has_access, error_response = check_location_access(request, leave_request.location_id)
        if not has_access:
            return error_response
        
        if leave_request.status != 'PENDING':
            return Response(
                {"detail": f"Leave request is already {leave_request.status.lower()}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        rejection_reason = request.data.get('rejection_reason', '')
        leave_request.status = 'REJECTED'
        leave_request.approved_by = request.user
        leave_request.approved_on = datetime.now()
        leave_request.rejection_reason = rejection_reason
        leave_request.save(update_fields=['status', 'approved_by', 'approved_on', 'rejection_reason'])
        
        serializer = self.get_serializer(leave_request)
        return Response(serializer.data)


class LeaveRequestBulkCreateView(AuthenticatedAPIView):
    """
    Bulk create leave requests for multiple employees with different dates and leave types.
    
    When admin creates leave requests via this API, they default to APPROVED status.
    Status can be changed later via update/approve/reject actions.
    """
    def post(self, request):
        serializer = LeaveRequestBulkCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        leave_requests_data = serializer.validated_data['leave_requests']
        created_requests = []
        errors = []
        
        for idx, leave_req_data in enumerate(leave_requests_data):
            try:
                employee_id = leave_req_data.get('employee_id')
                leave_type_id = leave_req_data.get('leave_type_id')
                start_date_str = leave_req_data.get('start_date')
                end_date_str = leave_req_data.get('end_date')
                reason = leave_req_data.get('reason', '')
                location_id = leave_req_data.get('location_id')
                
                # Get employee location if not provided
                if not location_id:
                    location_id = get_employee_location(employee_id)
                    if not location_id:
                        errors.append({
                            "row": idx + 1,
                            "error": "Employee not found or has no location",
                            "employee_id": str(employee_id) if employee_id else "N/A"
                        })
                        continue
                
                # Check location access
                has_access, error_response = check_location_access(request, location_id)
                if not has_access:
                    errors.append({
                        "row": idx + 1,
                        "error": "Access denied to employee's location",
                        "employee_id": str(employee_id) if employee_id else "N/A"
                    })
                    continue
                
                # Validate dates
                try:
                    start_date = datetime.strptime(str(start_date_str), '%Y-%m-%d').date()
                    end_date = datetime.strptime(str(end_date_str), '%Y-%m-%d').date()
                except ValueError:
                    errors.append({
                        "row": idx + 1,
                        "error": f"Invalid date format. Use YYYY-MM-DD",
                        "start_date": str(start_date_str),
                        "end_date": str(end_date_str)
                    })
                    continue
                
                if end_date < start_date:
                    errors.append({
                        "row": idx + 1,
                        "error": "end_date must be >= start_date",
                        "start_date": str(start_date),
                        "end_date": str(end_date)
                    })
                    continue
                
                # Calculate total_days
                total_days = (end_date - start_date).days + 1
                
                # Get employee and leave_type objects
                try:
                    employee = Employee.objects.get(pk=employee_id)
                    leave_type = LeaveType.objects.get(pk=leave_type_id, is_active=True)
                    location = Location.objects.get(pk=location_id)
                except Employee.DoesNotExist:
                    errors.append({
                        "row": idx + 1,
                        "error": f"Employee not found",
                        "employee_id": str(employee_id)
                    })
                    continue
                except LeaveType.DoesNotExist:
                    errors.append({
                        "row": idx + 1,
                        "error": f"Leave type not found or inactive",
                        "leave_type_id": str(leave_type_id)
                    })
                    continue
                except Location.DoesNotExist:
                    errors.append({
                        "row": idx + 1,
                        "error": f"Location not found",
                        "location_id": str(location_id)
                    })
                    continue
                
                # Verify employee belongs to location
                if employee.location_id != location_id:
                    errors.append({
                        "row": idx + 1,
                        "error": "Employee does not belong to the specified location",
                        "employee_id": str(employee_id),
                        "location_id": str(location_id)
                    })
                    continue
                
                # Create leave request (defaults to APPROVED when admin creates)
                leave_request = LeaveRequest.objects.create(
                    employee=employee,
                    location=location,
                    leave_type=leave_type,
                    start_date=start_date,
                    end_date=end_date,
                    total_days=total_days,
                    reason=reason,
                    status='APPROVED',  # Default to APPROVED when admin creates
                    applied_by=request.user,
                    approved_by=request.user,  # Admin who creates is also the approver
                    approved_on=datetime.now(),
                    is_active=True
                )
                created_requests.append(leave_request)
                
            except Exception as e:
                errors.append({
                    "row": idx + 1,
                    "error": f"Unexpected error: {str(e)}",
                    "data": leave_req_data
                })
                continue
        
        if errors and not created_requests:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        
        result_serializer = LeaveRequestSerializer(created_requests, many=True)
        response_data = {
            "message": f"Successfully created {len(created_requests)} leave request(s)",
            "created": result_serializer.data,
            "count": len(created_requests)
        }
        
        if errors:
            response_data["errors"] = errors
            response_data["error_count"] = len(errors)
        
        status_code = status.HTTP_201_CREATED if created_requests else status.HTTP_400_BAD_REQUEST
        return Response(response_data, status=status_code)


# ==================== LeaveBalance APIs ====================

class LeaveBalanceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for LeaveBalance CRUD operations.
    Provides list, create, retrieve, update, partial_update, and destroy.
    """
    queryset = LeaveBalance.objects.all()
    serializer_class = LeaveBalanceSerializer
    authentication_classes = [SimpleTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Filter queryset based on user role and query params"""
        queryset = super().get_queryset()
        employee_id = self.request.query_params.get('employee_id')
        location_id = self.request.query_params.get('location_id')
        year = self.request.query_params.get('year')
        
        if self.request.user.role == User.Role.ADMIN:
            if not self.request.user.location_id:
                return queryset.none()  # Return empty queryset, error handled in list()
            queryset = queryset.filter(location=self.request.user.location)
        elif location_id:
            queryset = queryset.filter(location_id=location_id)
        
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        if year:
            queryset = queryset.filter(year=int(year))
        
        return queryset
    
    def list(self, request, *args, **kwargs):
        """
        List all leave balances with access control.
        Auto-creates missing balances for all employees and leave types if not present.
        """
        from datetime import date
        
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        
        # Determine location
        location_id = None
        if request.user.role == User.Role.ADMIN:
            location_id = request.user.location_id
        else:
            location_id = request.query_params.get('location_id')
        
        if not location_id:
            return Response(
                {"detail": "location_id is required for listing leave balances."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Check access
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        # Check if leave types are configured for this location
        leave_types = LeaveType.objects.filter(location_id=location_id, is_active=True)
        if not leave_types.exists():
            return Response(
                {
                    "detail": "Leave types are not configured for this location. Please configure leave types first.",
                    "error_code": "NO_LEAVE_TYPES_CONFIGURED"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Get year (default to current year if not provided)
        year_str = request.query_params.get('year')
        year = int(year_str) if year_str else date.today().year
        
        # Get employee_id filter (if any)
        employee_id = request.query_params.get('employee_id')
        
        # Get employees for this location
        if employee_id:
            try:
                employees = [Employee.objects.get(id=employee_id, location_id=location_id)]
            except Employee.DoesNotExist:
                return Response(
                    {"detail": f"Employee with id {employee_id} not found in this location."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            employees = Employee.objects.filter(location_id=location_id, is_deleted=False)
        
        # Auto-create missing balances
        created_count = 0
        for employee in employees:
            for leave_type in leave_types:
                balance, created = LeaveBalance.objects.get_or_create(
                    employee=employee,
                    location_id=location_id,
                    leave_type=leave_type,
                    year=year,
                    defaults={
                        'total_allocated': leave_type.max_days_per_year if leave_type.max_days_per_year is not None else 0,
                        'used_days': 0,
                        'pending_days': 0,
                        'carry_forward_from_previous': 0,
                        'available_days': leave_type.max_days_per_year if leave_type.max_days_per_year is not None else 0,
                    }
                )
                if created:
                    created_count += 1
        
        # Now get the queryset (which will include the newly created balances)
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            if created_count > 0:
                response.data['created_balances'] = created_count
            return response
        
        serializer = self.get_serializer(queryset, many=True)
        response_data = serializer.data
        if created_count > 0:
            response_data = {
                'results': response_data,
                'created_balances': created_count
            }
        return Response(response_data)
    
    def create(self, request, *args, **kwargs):
        """Create leave balance with automatic available_days calculation"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        employee_id = serializer.validated_data['employee'].id
        location_id = get_employee_location(employee_id)
        if not location_id:
            return Response(
                {"detail": "Employee not found or has no location."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        # Check if leave types are configured for this location
        leave_types = LeaveType.objects.filter(location_id=location_id, is_active=True)
        if not leave_types.exists():
            return Response(
                {
                    "detail": "Leave types are not configured for this location. Please configure leave types first.",
                    "error_code": "NO_LEAVE_TYPES_CONFIGURED"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Validate that the leave_type belongs to the location
        leave_type = serializer.validated_data['leave_type']
        if leave_type.location_id != location_id or not leave_type.is_active:
            return Response(
                {
                    "detail": "Leave type not found or not active for this location.",
                    "error_code": "LEAVE_TYPE_NOT_FOUND"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Calculate available_days
        total_allocated = serializer.validated_data['total_allocated']
        carry_forward = serializer.validated_data.get('carry_forward_from_previous', 0)
        used = serializer.validated_data.get('used_days', 0)
        pending = serializer.validated_data.get('pending_days', 0)
        available = total_allocated + carry_forward - used - pending
        
        serializer.save(available_days=available)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve leave balance with access control.
        Auto-creates the balance if it doesn't exist.
        """
        from datetime import date
        
        # First, try to get the object normally
        try:
            instance = self.get_object()
            has_access, error_response = check_location_access(request, instance.location_id)
            if not has_access:
                return error_response
            return super().retrieve(request, *args, **kwargs)
        except Exception as e:
            # Object doesn't exist, need employee_id and leave_type_id to auto-create
            # Get employee_id and leave_type_id from query params
            employee_id = request.query_params.get('employee_id')
            leave_type_id = request.query_params.get('leave_type_id')
            year_str = request.query_params.get('year')
            year = int(year_str) if year_str else date.today().year
            
            if not employee_id or not leave_type_id:
                # Return 404 if we can't auto-create
                return Response(
                    {
                        "detail": "Leave balance not found. To auto-create, please provide employee_id, leave_type_id, and year (optional) in query params.",
                        "error_code": "BALANCE_NOT_FOUND"
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            
            try:
                employee = Employee.objects.get(id=employee_id)
                location_id = employee.location_id
                if not location_id:
                    return Response(
                        {"detail": "Employee does not have a location assigned."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                
                # Check access
                has_access, error_response = check_location_access(request, location_id)
                if not has_access:
                    return error_response
                
                # Check if leave types are configured
                leave_types = LeaveType.objects.filter(location_id=location_id, is_active=True)
                if not leave_types.exists():
                    return Response(
                        {
                            "detail": "Leave types are not configured for this location. Please configure leave types first.",
                            "error_code": "NO_LEAVE_TYPES_CONFIGURED"
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                
                # Check if leave type exists and is active
                try:
                    leave_type = LeaveType.objects.get(id=leave_type_id, location_id=location_id, is_active=True)
                except LeaveType.DoesNotExist:
                    return Response(
                        {
                            "detail": "Leave type not found or not active for this location.",
                            "error_code": "LEAVE_TYPE_NOT_FOUND"
                        },
                        status=status.HTTP_404_NOT_FOUND,
                    )
                
                # Auto-create the balance
                balance, created = LeaveBalance.objects.get_or_create(
                    employee=employee,
                    location_id=location_id,
                    leave_type=leave_type,
                    year=year,
                    defaults={
                        'total_allocated': leave_type.max_days_per_year if leave_type.max_days_per_year is not None else 0,
                        'used_days': 0,
                        'pending_days': 0,
                        'carry_forward_from_previous': 0,
                        'available_days': leave_type.max_days_per_year if leave_type.max_days_per_year is not None else 0,
                    }
                )
                
                serializer = self.get_serializer(balance)
                return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
                
            except Employee.DoesNotExist:
                return Response(
                    {"detail": "Employee not found.", "error_code": "EMPLOYEE_NOT_FOUND"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            except ValueError as ve:
                return Response(
                    {"detail": f"Invalid year format: {str(ve)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as ex:
                return Response(
                    {"detail": f"Error auto-creating balance: {str(ex)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
    
    def update(self, request, *args, **kwargs):
        """Update leave balance with access control and recalculate available_days"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Recalculate available_days
        total_allocated = serializer.validated_data.get('total_allocated', instance.total_allocated)
        carry_forward = serializer.validated_data.get('carry_forward_from_previous', instance.carry_forward_from_previous)
        used = serializer.validated_data.get('used_days', instance.used_days)
        pending = serializer.validated_data.get('pending_days', instance.pending_days)
        available = total_allocated + carry_forward - used - pending
        
        serializer.save(available_days=available)
        return Response(serializer.data)
    
    def partial_update(self, request, *args, **kwargs):
        """Partial update leave balance with access control and recalculate available_days"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        
        # Recalculate available_days
        total_allocated = serializer.validated_data.get('total_allocated', instance.total_allocated)
        carry_forward = serializer.validated_data.get('carry_forward_from_previous', instance.carry_forward_from_previous)
        used = serializer.validated_data.get('used_days', instance.used_days)
        pending = serializer.validated_data.get('pending_days', instance.pending_days)
        available = total_allocated + carry_forward - used - pending
        
        serializer.save(available_days=available)
        return Response(serializer.data)
    
    def destroy(self, request, *args, **kwargs):
        """Delete leave balance with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().destroy(request, *args, **kwargs)


# ==================== ManualAttendance APIs ====================

class ManualAttendanceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for ManualAttendance CRUD operations.
    Provides list, create, retrieve, update, partial_update, and destroy.
    """
    queryset = ManualAttendance.objects.all()
    serializer_class = ManualAttendanceSerializer
    authentication_classes = [SimpleTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """Filter queryset based on user role and query params"""
        queryset = super().get_queryset()
        employee_id = self.request.query_params.get('employee_id')
        location_id = self.request.query_params.get('location_id')
        month = self.request.query_params.get('month')  # YYYY-MM
        date_str = self.request.query_params.get('date')  # YYYY-MM-DD
        
        if self.request.user.role == User.Role.ADMIN:
            if not self.request.user.location_id:
                return queryset.none()  # Return empty queryset, error handled in list()
            queryset = queryset.filter(location=self.request.user.location)
        elif location_id:
            queryset = queryset.filter(location_id=location_id)
        
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        if month:
            year, month_num = map(int, month.split('-'))
            queryset = queryset.filter(attendance_date__year=year, attendance_date__month=month_num)
        if date_str:
            queryset = queryset.filter(attendance_date=date_str)
        
        return queryset
    
    def list(self, request, *args, **kwargs):
        """List all manual attendance records with access control"""
        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().list(request, *args, **kwargs)
    
    def create(self, request, *args, **kwargs):
        """Create manual attendance record"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        employee_id = serializer.validated_data['employee'].id
        location_id = get_employee_location(employee_id)
        if not location_id:
            return Response(
                {"detail": "Employee not found or has no location."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        serializer.save(marked_by=request.user)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    def retrieve(self, request, *args, **kwargs):
        """Retrieve manual attendance record with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().retrieve(request, *args, **kwargs)
    
    def update(self, request, *args, **kwargs):
        """Update manual attendance record with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().update(request, *args, **kwargs)
    
    def partial_update(self, request, *args, **kwargs):
        """Partial update manual attendance record with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().partial_update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        """Delete manual attendance record with access control"""
        instance = self.get_object()
        has_access, error_response = check_location_access(request, instance.location_id)
        if not has_access:
            return error_response
        return super().destroy(request, *args, **kwargs)


class ManualAttendanceBulkCreateView(AuthenticatedAPIView):
    """
    Bulk create manual attendance for multiple employees with different dates.
    
    Request format:
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
                    {"date": "2025-01-21", "status": "HALF_DAY"}
                ]
            }
        ]
    }
    """
    def post(self, request):
        serializer = ManualAttendanceBulkCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        attendances_data = serializer.validated_data['attendances']
        created_attendances = []
        errors = []
        
        for idx, attendance_data in enumerate(attendances_data):
            employee_id = attendance_data.get('employee_id')
            dates = attendance_data.get('dates', [])
            
            if not employee_id:
                errors.append(f"attendances[{idx}]: 'employee_id' is required")
                continue
            
            location_id = get_employee_location(employee_id)
            if not location_id:
                errors.append(f"attendances[{idx}]: Employee not found or has no location")
                continue
            
            has_access, error_response = check_location_access(request, location_id)
            if not has_access:
                errors.append(f"attendances[{idx}]: Access denied")
                continue
            
            # Create attendance for each date
            for date_data in dates:
                date_str = date_data.get('date')
                status_val = date_data.get('status', 'PRESENT')
                check_in_time = date_data.get('check_in_time')
                check_out_time = date_data.get('check_out_time')
                remarks = date_data.get('remarks', '')
                leave_request_id = date_data.get('leave_request_id')
                
                try:
                    attendance_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
                    # Get or create (update if exists)
                    attendance, created = ManualAttendance.objects.update_or_create(
                        employee_id=employee_id,
                        location_id=location_id,
                        attendance_date=attendance_date,
                        defaults={
                            'status': status_val,
                            'check_in_time': check_in_time,
                            'check_out_time': check_out_time,
                            'remarks': remarks,
                            'leave_request_id': leave_request_id,
                            'marked_by': request.user,
                            'is_override': True
                        }
                    )
                    created_attendances.append(attendance)
                except ValueError:
                    errors.append(f"attendances[{idx}]: Invalid date format '{date_str}'. Use YYYY-MM-DD")
                except Exception as e:
                    errors.append(f"attendances[{idx}]: Error creating attendance for date '{date_str}': {str(e)}")
        
        if errors and not created_attendances:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        
        result_serializer = ManualAttendanceSerializer(created_attendances, many=True)
        response_data = {"created": result_serializer.data, "count": len(created_attendances)}
        if errors:
            response_data["errors"] = errors
        
        return Response(response_data, status=status.HTTP_201_CREATED)


class ManualAttendanceBulkUpdateView(AuthenticatedAPIView):
    """Bulk update manual attendance"""
    def patch(self, request):
        serializer = ManualAttendanceBulkUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        attendances_data = serializer.validated_data['attendances']
        updated_attendances = []
        errors = []
        
        for idx, attendance_data in enumerate(attendances_data):
            attendance_id = attendance_data.get('id')
            if not attendance_id:
                errors.append(f"attendances[{idx}]: 'id' is required")
                continue
            
            try:
                attendance = ManualAttendance.objects.get(pk=attendance_id)
                has_access, error_response = check_location_access(request, attendance.location_id)
                if not has_access:
                    errors.append(f"attendances[{idx}]: Access denied")
                    continue
                
                # Remove id from data before updating
                update_data = {k: v for k, v in attendance_data.items() if k != 'id'}
                attendance_serializer = ManualAttendanceSerializer(attendance, data=update_data, partial=True)
                if attendance_serializer.is_valid():
                    updated_attendance = attendance_serializer.save()
                    updated_attendances.append(updated_attendance)
                else:
                    errors.append(f"attendances[{idx}]: {attendance_serializer.errors}")
            except ManualAttendance.DoesNotExist:
                errors.append(f"attendances[{idx}]: Attendance with id '{attendance_id}' not found")
            except Exception as e:
                errors.append(f"attendances[{idx}]: Error updating: {str(e)}")
        
        if errors and not updated_attendances:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        
        result_serializer = ManualAttendanceSerializer(updated_attendances, many=True)
        response_data = {"updated": result_serializer.data, "count": len(updated_attendances)}
        if errors:
            response_data["errors"] = errors
        
        return Response(response_data)


class ManualAttendanceBulkDeleteView(AuthenticatedAPIView):
    """Bulk delete manual attendance"""
    def post(self, request):
        serializer = ManualAttendanceBulkDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        attendance_ids = serializer.validated_data['attendance_ids']
        deleted_count = 0
        errors = []
        
        for attendance_id in attendance_ids:
            try:
                attendance = ManualAttendance.objects.get(pk=attendance_id)
                has_access, error_response = check_location_access(request, attendance.location_id)
                if not has_access:
                    errors.append(f"Attendance {attendance_id}: Access denied")
                    continue
                
                attendance.delete()
                deleted_count += 1
            except ManualAttendance.DoesNotExist:
                errors.append(f"Attendance {attendance_id}: Not found")
            except Exception as e:
                errors.append(f"Attendance {attendance_id}: Error deleting: {str(e)}")
        
        response_data = {"deleted_count": deleted_count}
        if errors:
            response_data["errors"] = errors
        
        return Response(response_data)


class ManualAttendanceBulkMarkView(AuthenticatedAPIView):
    """
    Advanced bulk mark attendance operations.
    
    Supports:
    - type="all": Mark all employees for a location
    - type="except": Mark all employees except specified IDs
    - type="specific": Mark only specified employee IDs
    
    Status can be: PRESENT, ABSENT, HALF_DAY, HOLIDAY, WEEKOFF, LEAVE
    """
    def post(self, request):
        serializer = ManualAttendanceBulkMarkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        attendance_date = serializer.validated_data['date']
        location_id = serializer.validated_data['location_id']
        mark_type = serializer.validated_data['type']
        status_val = serializer.validated_data['status']
        check_in_time = serializer.validated_data.get('check_in_time')
        check_out_time = serializer.validated_data.get('check_out_time')
        remarks = serializer.validated_data.get('remarks')
        
        # Check location access
        has_access, error_response = check_location_access(request, location_id)
        if not has_access:
            return error_response
        
        try:
            location = Location.objects.get(pk=location_id)
        except Location.DoesNotExist:
            return Response(
                {"detail": "Location not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get employees based on type
        employees = Employee.objects.filter(location=location)
        
        if mark_type == 'except':
            except_ids = serializer.validated_data.get('except_employee_ids', [])
            employees = employees.exclude(id__in=except_ids)
        elif mark_type == 'specific':
            employee_ids = serializer.validated_data.get('employee_ids', [])
            employees = employees.filter(id__in=employee_ids)
        # type == 'all' uses all employees (no filter needed)
        
        created_attendances = []
        errors = []
        
        for employee in employees:
            try:
                # Use update_or_create to handle existing records
                attendance, created = ManualAttendance.objects.update_or_create(
                    employee=employee,
                    location=location,
                    attendance_date=attendance_date,
                    defaults={
                        'status': status_val,
                        'check_in_time': check_in_time,
                        'check_out_time': check_out_time,
                        'remarks': remarks or f"Bulk marked as {status_val}",
                        'marked_by': request.user,
                        'is_override': True
                    }
                )
                created_attendances.append(attendance)
            except Exception as e:
                errors.append({
                    "employee_id": str(employee.id),
                    "employee_name": employee.name,
                    "error": str(e)
                })
        
        result_serializer = ManualAttendanceSerializer(created_attendances, many=True)
        response_data = {
            "message": f"Successfully marked {len(created_attendances)} employee(s) as {status_val}",
            "created": result_serializer.data,
            "count": len(created_attendances),
            "date": str(attendance_date),
            "location_id": str(location_id),
            "type": mark_type,
            "status": status_val
        }
        
        if errors:
            response_data["errors"] = errors
            response_data["error_count"] = len(errors)
        
        return Response(response_data, status=status.HTTP_201_CREATED)


