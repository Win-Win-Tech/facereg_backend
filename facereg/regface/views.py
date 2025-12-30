import base64
import logging
import os
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

import face_recognition
import numpy as np
from django.conf import settings
from django.db.models import Min, Max
from django.utils import timezone
from django.utils.timezone import localtime
from openpyxl import Workbook
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from .face_utils import get_face_encoding
from .face_index import FaceIndexManager
from .models import AttendanceLog, AuthToken, Employee, Location, PayrollRecord, User, UserSite, Site, Shift, Assignment
from .serializers import (
    EmployeeRegisterSerializer,
    EmployeeListSerializer,
    EmployeeSerializer,
    EmployeeUpdateSerializer,
    FaceUploadSerializer,
    LocationSerializer,
    UserSerializer,
    ShiftSerializer,
    SiteSerializer,
    AssignmentSerializer,
    UserSiteSerializer,
)
from .authentication import SimpleTokenAuthentication
from django.conf import settings
from django.utils import timezone
logger = logging.getLogger(__name__)

logger.info(f"USE_TZ={settings.USE_TZ}, TIME_ZONE={settings.TIME_ZONE}")
logger.info(f"timezone.now()={timezone.localtime()}")


def is_superadmin(user: User) -> bool:
    return getattr(user, "role", None) == User.Role.SUPERADMIN


class AuthenticatedAPIView(APIView):
    authentication_classes = [SimpleTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]


class LocationListCreateView(AuthenticatedAPIView):
    def get(self, request):
        include_deleted = request.query_params.get("include_deleted") == "true"
        locations = Location.objects.all()
        if not is_superadmin(request.user):
            if not request.user.location_id:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            locations = locations.filter(pk=request.user.location_id, is_deleted=False)
        elif not include_deleted:
            locations = locations.filter(is_deleted=False)
        serializer = LocationSerializer(locations, many=True)
        return Response(serializer.data)

    def post(self, request):
        if not is_superadmin(request.user):
            return Response(
                {"detail": "Only super admins can create locations."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LocationSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LocationDetailView(AuthenticatedAPIView):
    def get_object(self, pk):
        try:
            return Location.objects.get(pk=pk, is_deleted=False)
        except Location.DoesNotExist:
            return None

    def get(self, request, pk):
        location = self.get_object(pk)
        if not location:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and location.id != request.user.location_id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = LocationSerializer(location)
        return Response(serializer.data)

    def put(self, request, pk):
        return self.patch(request, pk)

    def patch(self, request, pk):
        if not is_superadmin(request.user):
            return Response(
                {"detail": "Only super admins can update locations."},
                status=status.HTTP_403_FORBIDDEN,
            )
        location = self.get_object(pk)
        if not location:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = LocationSerializer(location, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        if not is_superadmin(request.user):
            return Response(
                {"detail": "Only super admins can delete locations."},
                status=status.HTTP_403_FORBIDDEN,
            )
        location = self.get_object(pk)
        if not location:
            return Response(status=status.HTTP_404_NOT_FOUND)
        location.is_deleted = True
        location.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserListCreateView(AuthenticatedAPIView):
    def get_queryset(self, request):
        queryset = User.objects.filter(is_deleted=False)
        role = request.query_params.get("role")
        location_id = request.query_params.get("location_id")
        is_active = request.query_params.get("is_active")

        if role:
            queryset = queryset.filter(role=role)
        if location_id:
            queryset = queryset.filter(location_id=location_id)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        if request.user.role == User.Role.ADMIN:
            queryset = queryset.filter(location=request.user.location).exclude(
                role=User.Role.SUPERADMIN
            )
        return queryset

    def get(self, request):
        users = self.get_queryset(request)
        serializer = UserSerializer(users, many=True)
        return Response(serializer.data)

    def post(self, request):
        if not is_superadmin(request.user):
            return Response(
                {"detail": "Only super admins can create users."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            output = UserSerializer(user).data
            return Response(output, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserDetailView(AuthenticatedAPIView):
    def get_object(self, pk):
        try:
            return User.objects.get(pk=pk, is_deleted=False)
        except User.DoesNotExist:
            return None

    def get(self, request, pk):
        user = self.get_object(pk)
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if request.user.role == User.Role.ADMIN and user.location != request.user.location:
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = UserSerializer(user)
        return Response(serializer.data)

    def put(self, request, pk):
        return self.patch(request, pk)

    def patch(self, request, pk):
        user = self.get_object(pk)
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if request.user.role == User.Role.ADMIN and request.user.id != user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if request.user.role == User.Role.ADMIN:
            request.data.setdefault("role", User.Role.ADMIN)

        serializer = UserSerializer(user, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        if request.user.role == User.Role.ADMIN:
            new_location = serializer.validated_data.get("location", user.location)
            if new_location != request.user.location:
                return Response(
                    {"detail": "Admins cannot change their assigned location."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        user = self.get_object(pk)
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not is_superadmin(request.user) and request.user.id != user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        user.is_deleted = True
        user.is_active = False
        user.save(update_fields=["is_deleted", "is_active", "updated_at"])
        AuthToken.objects.filter(user=user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get("email")
        password = request.data.get("password")
        if not email or not password:
            return Response(
                {"detail": "Email and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email=email, is_deleted=False)
        except User.DoesNotExist:
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.check_password(password):
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"detail": "User account is inactive."},
                status=status.HTTP_403_FORBIDDEN,
            )

        AuthToken.objects.filter(user=user).delete()
        token = AuthToken.objects.create(user=user)
        data = UserSerializer(user).data
        data["token"] = token.key
        return Response(data)


class LogoutView(AuthenticatedAPIView):
    def post(self, request):
        AuthToken.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeListView(AuthenticatedAPIView):
    def get(self, request):
        queryset = Employee.objects.select_related("location")
        location_id = request.query_params.get("location_id")
        if request.user.role == User.Role.ADMIN:
            queryset = queryset.filter(location=request.user.location)
        elif location_id:
            queryset = queryset.filter(location_id=location_id)
        serializer = EmployeeListSerializer(queryset, many=True)
        return Response(serializer.data)


class EmployeeDetailView(AuthenticatedAPIView):
    def get_object(self, request, pk):
        try:
            employee = Employee.objects.select_related("location").get(pk=pk)
        except Employee.DoesNotExist:
            return None
        if request.user.role == User.Role.ADMIN and employee.location != request.user.location:
            return None
        return employee

    def get(self, request, pk):
        employee = self.get_object(request, pk)
        if not employee:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = EmployeeSerializer(employee)
        return Response(serializer.data)

    def put(self, request, pk):
        return self.patch(request, pk)

    def patch(self, request, pk):
        employee = self.get_object(request, pk)
        if not employee:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = EmployeeUpdateSerializer(employee, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        extra_kwargs = {}
        if request.user.role == User.Role.ADMIN:
            if not request.user.location:
                return Response(
                    {"detail": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            new_location = serializer.validated_data.get("location", request.user.location)
            if new_location and new_location != request.user.location:
                return Response(
                    {"detail": "Admins cannot move employees to a different location."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            extra_kwargs["location"] = request.user.location

        employee = serializer.save(**extra_kwargs)

        face_file = request.FILES.get("face_image")
        profile_file = request.FILES.get("profile_photo")
        updated_fields = []

        face_bytes = None
        if face_file:
            face_bytes = face_file.read()
            encoding = get_face_encoding(face_bytes)
            if encoding is None:
                return Response(
                    {"face_image": ["No face detected in provided image."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            employee.face_encoding = encoding.tobytes()
            updated_fields.append("face_encoding")
            # Update FAISS Index
            FaceIndexManager.get_instance().add_employee(employee.id, employee.face_encoding)

        if profile_file:
            employee.photo = profile_file.read()
            updated_fields.append("photo")
        elif face_bytes and not employee.photo:
            employee.photo = face_bytes
            if "photo" not in updated_fields:
                updated_fields.append("photo")

        if updated_fields:
            employee.save(update_fields=updated_fields)

        return Response(EmployeeSerializer(employee).data)

    def delete(self, request, pk):
        employee = self.get_object(request, pk)
        if not employee:
            return Response(status=status.HTTP_404_NOT_FOUND)
        employee.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ShiftListCreateView(AuthenticatedAPIView):


    def get(self, request):
        shifts = Shift.objects.filter(is_deleted=False)

        location_id = request.query_params.get("location_id")
        site_id = request.query_params.get("site_id")

        if site_id:
            shifts = shifts.filter(sites__id=site_id)

        if location_id:
            shifts = shifts.filter(sites__location_id=location_id)

        shifts = shifts.order_by("shift_name").distinct()

        serializer = ShiftSerializer(shifts, many=True)
        return Response(serializer.data)

    def post(self, request):
        """
        Create one or many shifts.

        - Single shift: POST /api/shifts/ with a JSON object
        - Bulk shifts:  POST /api/shifts/ with a JSON array of objects
        """
        data = request.data
        many = isinstance(data, list)

        serializer = ShiftSerializer(data=data, many=many)
        if serializer.is_valid():
            shifts = serializer.save(created_by=request.user)
            return Response(
                ShiftSerializer(shifts, many=many).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ShiftDetailView(AuthenticatedAPIView):
    """Retrieve, update, and delete specific shifts."""
    def get_object(self, pk):
        try:
            return Shift.objects.get(pk=pk, is_deleted=False)
        except Shift.DoesNotExist:
            return None
    
    def get(self, request, pk):
        shift = self.get_object(pk)
        if not shift:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ShiftSerializer(shift)
        return Response(serializer.data)
    
    def patch(self, request, pk):
        shift = self.get_object(pk)
        if not shift:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        serializer = ShiftSerializer(shift, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save(modified_by=request.user)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def put(self, request, pk):
        return self.patch(request, pk)
    
    def delete(self, request, pk):
        shift = self.get_object(pk)
        if not shift:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        # Check if shift is assigned to any active employees
        active_assignments_count = Assignment.objects.filter(
            shift=shift,
            is_deleted=False
        ).count()
        
        if active_assignments_count > 0:
            return Response(
                {
                    "error": "Cannot delete shift",
                    "message": f"This shift is currently assigned to {active_assignments_count} employee(s). Please reassign them to a different shift before deleting.",
                    "assigned_employees_count": active_assignments_count
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        shift.is_deleted = True
        shift.deleted_by = request.user
        shift.save(update_fields=['is_deleted', 'deleted_by', 'modified_on', 'modified_by'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SiteBulkShiftAssignView(AuthenticatedAPIView):
    def post(self, request, pk):
       
        try:
            site = Site.objects.get(pk=pk, is_deleted=False)
        except Site.DoesNotExist:
            return Response({"error": "Site not found"}, status=status.HTTP_404_NOT_FOUND)

        # Admin users can only operate within their location
        if request.user.role == User.Role.ADMIN and site.location_id != request.user.location_id:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data
        if isinstance(payload, list):
            shifts_to_create = payload
            shift_ids = []
        else:
            shifts_to_create = payload.get("shifts", []) or []
            shift_ids = payload.get("shift_ids", []) or []

        created_shifts = []
        assigned_shifts = []

        # Create new shifts (do NOT set site on Shift; use M2M on Site)
        if shifts_to_create:
            serializer = ShiftSerializer(data=shifts_to_create, many=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            for valid in serializer.validated_data:
                shift_obj = Shift.objects.create(
                    shift_name=valid.get("shift_name"),
                    start_time=valid.get("start_time"),
                    end_time=valid.get("end_time"),
                    grace_timing=valid.get("grace_timing", 30),
                    created_by=request.user,
                    modified_by=request.user,
                )
                created_shifts.append(shift_obj)
                site.shifts.add(shift_obj)

        # Attach existing shifts by IDs to this site (M2M)
        if shift_ids:
            # Accept string or list
            if isinstance(shift_ids, str):
                shift_ids = [shift_ids]
            qs = Shift.objects.filter(id__in=shift_ids, is_deleted=False)
            for shift in qs:
                site.shifts.add(shift)
                try:
                    shift.modified_by = request.user
                    shift.save(update_fields=["modified_by", "modified_on"])
                except Exception:
                    shift.save()
                assigned_shifts.append(shift)

        result = {
            "created_count": len(created_shifts),
            "assigned_count": len(assigned_shifts),
            "created": ShiftSerializer(created_shifts, many=True).data,
            "assigned": ShiftSerializer(assigned_shifts, many=True).data,
        }

        return Response(result, status=status.HTTP_200_OK)

    def put(self, request, pk):
        """Replace the site's assigned shifts with provided shift ids and/or newly created shifts."""
        try:
            site = Site.objects.get(pk=pk, is_deleted=False)
        except Site.DoesNotExist:
            return Response({"error": "Site not found"}, status=status.HTTP_404_NOT_FOUND)

        if request.user.role == User.Role.ADMIN and site.location_id != request.user.location_id:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data
        shifts_to_create = payload.get("shifts", []) or []
        shift_ids = payload.get("shift_ids", []) or []

        new_shift_objs = []
        if shifts_to_create:
            serializer = ShiftSerializer(data=shifts_to_create, many=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            for valid in serializer.validated_data:
                shift_obj = Shift.objects.create(
                    shift_name=valid.get("shift_name"),
                    start_time=valid.get("start_time"),
                    end_time=valid.get("end_time"),
                    grace_timing=valid.get("grace_timing", 30),
                    created_by=request.user,
                    modified_by=request.user,
                )
                new_shift_objs.append(shift_obj)

        # Build final list of shift pks to set
        final_shift_ids = [str(s.id) for s in new_shift_objs]
        if isinstance(shift_ids, list):
            final_shift_ids += [str(s) for s in shift_ids]
        elif isinstance(shift_ids, str):
            final_shift_ids.append(shift_ids)

        # Validate provided existing shift ids
        existing_qs = Shift.objects.filter(id__in=final_shift_ids, is_deleted=False)
        site.shifts.set(existing_qs)

        return Response({"assigned_count": site.shifts.count()}, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        """Remove specified shift ids from the site. If no shift_ids provided, clear all."""
        try:
            site = Site.objects.get(pk=pk, is_deleted=False)
        except Site.DoesNotExist:
            return Response({"error": "Site not found"}, status=status.HTTP_404_NOT_FOUND)

        if request.user.role == User.Role.ADMIN and site.location_id != request.user.location_id:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        shift_ids = request.data.get("shift_ids", None)
        if shift_ids is None:
            site.shifts.clear()
            return Response({"removed": "all"}, status=status.HTTP_200_OK)

        if isinstance(shift_ids, str):
            shift_ids = [shift_ids]

        qs = Shift.objects.filter(id__in=shift_ids)
        for s in qs:
            site.shifts.remove(s)

        return Response({"removed_count": len(shift_ids)}, status=status.HTTP_200_OK)


class SiteListCreateView(AuthenticatedAPIView):
 
    def get(self, request):
        sites = Site.objects.filter(is_deleted=False)

        location = request.query_params.get("location")
        location_id = request.query_params.get("location_id") or location

        if location_id:
            sites = sites.filter(location_id=location_id)

        sites = sites.order_by("site_name")

        serializer = SiteSerializer(sites, many=True)
        return Response(serializer.data)
    def post(self, request):
        serializer = SiteSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SiteDetailView(AuthenticatedAPIView):
    """Retrieve, update, and delete specific sites."""
    def get_object(self, pk):
        try:
            return Site.objects.get(pk=pk, is_deleted=False)
        except Site.DoesNotExist:
            return None
    
    def get(self, request, pk):
        site = self.get_object(pk)
        if not site:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = SiteSerializer(site)
        return Response(serializer.data)
    
    def patch(self, request, pk):
        site = self.get_object(pk)
        if not site:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        serializer = SiteSerializer(site, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save(modified_by=request.user)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def put(self, request, pk):
        return self.patch(request, pk)
    
    def delete(self, request, pk):
        site = self.get_object(pk)
        if not site:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        site.is_deleted = True
        site.deleted_by = request.user
        site.save(update_fields=['is_deleted', 'deleted_by', 'modified_on', 'modified_by'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class AssignmentListCreateView(AuthenticatedAPIView):
    """List and create employee shift/site assignments."""
    def get(self, request):
        queryset = Assignment.objects.filter(is_deleted=False).select_related('user', 'shift', 'location')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')
        shift_id = request.query_params.get('shift_id')
        
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        if location_id:
            queryset = queryset.filter(location_id=location_id)
        if shift_id:
            queryset = queryset.filter(shift_id=shift_id)
        
        if request.user.role == User.Role.ADMIN:
            queryset = queryset.filter(location=request.user.location)
        
        serializer = AssignmentSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        data = request.data.copy()
        data['created_by'] = request.user.id
        
        serializer = AssignmentSerializer(data=data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BulkAssignmentView(AuthenticatedAPIView):
    """Bulk create shift assignments and site assignments for multiple employees."""
    def post(self, request):
       
        employee_ids = request.data.get('employee_ids', [])
        shift_id = request.data.get('shift_id')
        location_id = request.data.get('location_id')
        site_ids = request.data.get('site_ids', [])
        assignment_from_date = request.data.get('assignment_from_date')
        assignment_to_date = request.data.get('assignment_to_date')
        
        # Validation
        if not employee_ids or not isinstance(employee_ids, list):
            return Response(
                {"error": "employee_ids must be a non-empty list"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        shift = None
        if shift_id not in (None, '', 'null'):
            try:
                shift = Shift.objects.get(pk=shift_id, is_deleted=False)
            except Shift.DoesNotExist:
                return Response(
                    {"error": "Shift not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # `shift` is either a Shift instance or None (no-shift)
        
        # Verify location exists
        try:
            location = Location.objects.get(pk=location_id, is_deleted=False)
        except Location.DoesNotExist:
            return Response(
                {"error": "Location not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify sites exist if provided
        sites = []
        if site_ids:
            sites = list(Site.objects.filter(pk__in=site_ids, is_deleted=False).prefetch_related('shifts'))
            if len(sites) != len(site_ids):
                return Response(
                    {"error": "One or more sites not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        if shift is None and sites:
           
            site_shift_sets = []
            for s in sites:
                ids = set(s.shifts.values_list('id', flat=True))
                if ids:
                    site_shift_sets.append(ids)

            if site_shift_sets:
                common = set.intersection(*site_shift_sets) if len(site_shift_sets) > 1 else site_shift_sets[0]
                if len(common) == 1:
                    first = list(common)[0]
                    try:
                        shift = Shift.objects.get(pk=first, is_deleted=False)
                    except Shift.DoesNotExist:
                        shift = None
        
        # Create assignments
        created_assignments = []
        created_user_sites = []
        failed_assignments = []
        
        for employee_id in employee_ids:
            try:
                # Check if employee exists
                employee = Employee.objects.get(pk=employee_id)
                
                # Check if an assignment already exists for this employee at the location
                existing = Assignment.objects.filter(
                    user_id=employee_id,
                    location_id=location_id,
                    is_deleted=False
                ).first()

                if existing:
                    current_shift_id = existing.shift_id if hasattr(existing, 'shift_id') else (existing.shift.id if existing.shift else None)
                    new_shift_id = shift.id if shift else None
                    if current_shift_id != new_shift_id or existing.assignment_from_date != assignment_from_date or existing.assignment_to_date != assignment_to_date:
                        existing.shift = shift
                        existing.assignment_from_date = assignment_from_date
                        existing.assignment_to_date = assignment_to_date
                        existing.modified_by = request.user
                        try:
                            existing.modified_on = timezone.now()
                        except Exception:
                            pass
                        existing.save(update_fields=["shift", "assignment_from_date", "assignment_to_date", "modified_on", "modified_by"])
                    # Treat existing (created or updated) as an affected assignment
                    created_assignments.append(existing)
                else:
                    assignment = Assignment.objects.create(
                        user_id=employee_id,
                        shift=shift,
                        location_id=location_id,
                        assignment_from_date=assignment_from_date,
                        assignment_to_date=assignment_to_date,
                        created_by=request.user
                    )
                    created_assignments.append(assignment)
                
                # Create UserSite assignments if sites are provided
                if sites:
                    for site in sites:
                        existing_site_assign = UserSite.objects.filter(
                            user_id=employee_id,
                            site_id=site.id,
                            is_deleted=False
                        ).first()
                        
                        if not existing_site_assign:
                            user_site = UserSite.objects.create(
                                user_id=employee_id,
                                site_id=site.id,
                                created_by=request.user,
                                assigned_by=request.user
                            )
                            created_user_sites.append(user_site)
                
            except Employee.DoesNotExist:
                failed_assignments.append({
                    "employee_id": employee_id,
                    "error": "Employee not found"
                })
            except Exception as e:
                failed_assignments.append({
                    "employee_id": employee_id,
                    "error": str(e)
                })
        
        # Serialize created assignments
        assignment_serializer = AssignmentSerializer(created_assignments, many=True)
        user_site_serializer = UserSiteSerializer(created_user_sites, many=True)
        
        response_data = {
            "created_assignments": len(created_assignments),
            "created_site_assignments": len(created_user_sites),
            "failed": len(failed_assignments),
            "assignments": assignment_serializer.data,
            "site_assignments": user_site_serializer.data,
        }
        
        if failed_assignments:
            response_data["failed_details"] = failed_assignments
        
        return Response(response_data, status=status.HTTP_201_CREATED)


class AssignmentDetailView(AuthenticatedAPIView):
    """Update and delete assignments."""
    def get_object(self, pk):
        try:
            return Assignment.objects.get(pk=pk, is_deleted=False)
        except Assignment.DoesNotExist:
            return None

    def patch(self, request, pk):
        assignment = self.get_object(pk)
        if not assignment:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        data = request.data.copy()
        data['modified_by'] = request.user.id
        
        serializer = AssignmentSerializer(assignment, data=data, partial=True)
        if serializer.is_valid():
            serializer.save(modified_by=request.user)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        assignment = self.get_object(pk)
        if not assignment:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        assignment.is_deleted = True
        assignment.deleted_by = request.user
        assignment.save(update_fields=['is_deleted', 'deleted_by', 'modified_on', 'modified_by'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserSiteListCreateView(AuthenticatedAPIView):
    """List user site assignments and bulk assign sites."""
    def get(self, request, pk):
        queryset = UserSite.objects.filter(user_id=pk, is_deleted=False).select_related('user', 'site')
        
        serializer = UserSiteSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request, pk):
        # Bulk assign sites to user
        site_ids = request.data.get('site_ids', [])
        
        # Mark existing assignments as deleted
        UserSite.objects.filter(user_id=pk, is_deleted=False).update(
            is_deleted=True, 
            deleted_by=request.user,
            modified_on=timezone.now(),
            modified_by=request.user
        )
        
        # Create new assignments
        user_sites = []
        for site_id in site_ids:
            user_sites.append(UserSite(
                user_id=pk,
                site_id=site_id,
                assigned_by=request.user,
                created_by=request.user
            ))
        
        UserSite.objects.bulk_create(user_sites)
        
        queryset = UserSite.objects.filter(user_id=pk, is_deleted=False)
        serializer = UserSiteSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class UserSiteDetailView(AuthenticatedAPIView):
    """Delete individual user site assignments."""
    def get_object(self, pk):
        try:
            return UserSite.objects.get(pk=pk, is_deleted=False)
        except UserSite.DoesNotExist:
            return None

    def delete(self, request, pk):
        user_site = self.get_object(pk)
        if not user_site:
            return Response(status=status.HTTP_404_NOT_FOUND)
        
        user_site.is_deleted = True
        user_site.deleted_by = request.user
        user_site.save(update_fields=['is_deleted', 'deleted_by', 'modified_on', 'modified_by'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# class FaceAttendanceView(APIView):
#     permission_classes = [permissions.AllowAny]
#     def post(self, request):
#         serializer = FaceUploadSerializer(data=request.data)
#         if not serializer.is_valid():
#             logger.warning("Invalid face upload data: %s", serializer.errors)
#             return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#         image = serializer.validated_data["image"]
#         uploaded_encoding = get_face_encoding(image.read())

#         if uploaded_encoding is None:
#             logger.info("No face detected in uploaded image")
#             return Response({"error": "No face detected"}, status=status.HTTP_400_BAD_REQUEST)

#         employees = Employee.objects.only("id", "name", "face_encoding", "photo")
#         user = getattr(request, "user", None)
#         if isinstance(user, User) and user.role == User.Role.ADMIN:
#             employees = employees.filter(location=user.location)

#         known_encodings = []
#         employee_map = []

#         for emp in employees:
#             encoding = np.frombuffer(emp.face_encoding)
#             known_encodings.append(encoding)
#             employee_map.append(emp)

#         matches = face_recognition.compare_faces(
#             known_encodings, uploaded_encoding, tolerance=0.45
#         )
#         if True in matches:
#             best_match_index = matches.index(True)
#             matched_employee = employee_map[best_match_index]
#             today = date.today()
#             now = timezone.localtime()

#             logs_today = AttendanceLog.objects.filter(
#                 employee=matched_employee, timestamp__date=today
#             )
#             has_checkin = any(log.type == "checkin" for log in logs_today)
#             has_checkout = any(log.type == "checkout" for log in logs_today)

#             if not has_checkin:
#                 entry_type = "checkin"
#                 message = f"Welcome, {matched_employee.name.strip()}! Your check-in has been recorded."
#             elif not has_checkout:
#                 entry_type = "checkout"
#                 message = (
#                     f"Good job today, {matched_employee.name.strip()}! Your check-out is complete."
#                 )
#             else:
#                 logger.info(
#                     "Both checkin and checkout already marked for %s",
#                     matched_employee.name.strip(),
#                 )
#                 return Response(
#                     {
#                     "status": "Already marked",
#                     "message": "You've already completed both check-in and check-out for today.",
#                     "employee": matched_employee.name.strip(),
#                         "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
#                     },
#                     status=status.HTTP_200_OK,
#                 )

#             AttendanceLog.objects.create(employee=matched_employee, type=entry_type)
#             logger.info("%s marked for %s", entry_type.capitalize(), matched_employee.name.strip())

#             confidence = round(
#                 1 - face_recognition.face_distance(
#                 [known_encodings[best_match_index]], uploaded_encoding
#                 )[0],
#                 2,
#             )

#             photo_base64 = (
#                 base64.b64encode(matched_employee.photo).decode("utf-8")
#                 if matched_employee.photo
#                 else None
#             )

#             return Response(
#                 {
#                 "status": f"{entry_type.capitalize()} successful",
#                 "message": message,
#                 "employee": matched_employee.name.strip(),
#                 "confidence": confidence,
#                 "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
#                     "photo": f"data:image/jpeg;base64,{photo_base64}"
#                     if photo_base64
#                     else None,
#                 },
#                 status=status.HTTP_200_OK,
#             )

#         logger.info("Face not recognized")
#         return Response({"error": "Face not recognized"}, status=status.HTTP_404_NOT_FOUND)

# class FaceAttendanceView(APIView):
#     permission_classes = [permissions.AllowAny]

#     def post(self, request):
#         serializer = FaceUploadSerializer(data=request.data)
#         if not serializer.is_valid():
#             return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#         image_bytes = serializer.validated_data["image"].read()
#         uploaded_encoding = get_face_encoding(image_bytes)
#         if uploaded_encoding is None:
#             return Response({"error": "No face detected"}, status=status.HTTP_400_BAD_REQUEST)

#         employees = Employee.objects.only("id", "name", "face_encoding", "photo", "location")
#         user = getattr(request, "user", None)
#         if isinstance(user, User) and user.role == User.Role.ADMIN:
#             employees = employees.filter(location=user.location)

#         known_encodings, employee_map = [], []
#         for emp in employees:
#             if emp.face_encoding:
#                 known_encodings.append(np.frombuffer(emp.face_encoding))
#                 employee_map.append(emp)

#         if not known_encodings:
#             return Response({"error": "No registered employees"}, status=status.HTTP_404_NOT_FOUND)

#         distances = face_recognition.face_distance(known_encodings, uploaded_encoding)
#         best_match_index = np.argmin(distances)
#         if distances[best_match_index] > 0.45:
#             return Response({"error": "Face not recognized"}, status=status.HTTP_404_NOT_FOUND)

#         matched_employee = employee_map[best_match_index]
#         print("matched_employee",matched_employee)
#         today = date.today()
#         now = timezone.now()

#         # --- Attendance rules ---
#         assignment = Assignment.objects.filter(user_id=matched_employee.id, location_id=matched_employee.location_id).select_related("shift").first()
#         user_sites = UserSite.objects.filter(user_id=matched_employee.id).select_related("site")
#         location_sites = Site.objects.filter(location_id=matched_employee.location_id)

#         shift = assignment.shift if assignment else None
#         sites = [us.site for us in user_sites] if user_sites.exists() else list(location_sites)

#         # Geofence check
#         lat = float(request.data.get("latitude", 0))
#         lon = float(request.data.get("longitude", 0))
#         inside_any = any(self.within_radius(lat, lon, s) for s in sites)
#         if not inside_any:
#             return Response({"error": "Outside allowed site radius"}, status=status.HTTP_403_FORBIDDEN)

#         # Shift timing check
#         status_label = "Checked-in"
#         if shift:
#             in_base, in_grace, status_hint = self.in_shift_window(now, shift)
#             if not in_grace:
#                 return Response({"error": "Outside allowed shift window"}, status=status.HTTP_403_FORBIDDEN)
#             if status_hint:
#                 status_label = status_hint

#         # --- Auto checkin/checkout ---
#         logs_today = AttendanceLog.objects.filter(employee=matched_employee, timestamp__date=today)
#         has_checkin = logs_today.filter(type="checkin").exists()
#         has_checkout = logs_today.filter(type="checkout").exists()

#         if not has_checkin:
#             entry_type = "checkin"
#             message = f"Welcome, {matched_employee.name.strip()}! Your check-in has been recorded."
#         elif not has_checkout:
#             entry_type = "checkout"
#             message = f"Good job today, {matched_employee.name.strip()}! Your check-out is complete."
#         else:
#             return Response({
#                 "status": "Already marked",
#                 "message": "You've already completed both check-in and check-out for today.",
#                 "employee": matched_employee.name.strip(),
#                 "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
#             }, status=status.HTTP_200_OK)

#         AttendanceLog.objects.create(
#             employee=matched_employee,
#             type=entry_type,
#             timestamp=now,
#             site=nearest_site,                  # ✅ assign nearest site
#             location=matched_employee.location, # ✅ assign location
#             # status=status_label (optional if you add field)
#         )

#         confidence = round(1 - distances[best_match_index], 2)
#         photo_base64 = base64.b64encode(matched_employee.photo).decode("utf-8") if matched_employee.photo else None

#         return Response({
#             "status": f"{entry_type.capitalize()} successful",
#             "message": message,
#             "employee": matched_employee.name.strip(),
#             "confidence": confidence,
#             "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
#             "photo": f"data:image/jpeg;base64,{photo_base64}" if photo_base64 else None,
#             "attendance_status": status_label
#         }, status=status.HTTP_200_OK)

#     # --- Helpers ---
#     # def within_radius(self, lat, lon, site):
#     #     from math import radians, sin, cos, asin, sqrt
#     #     R = 6371000.0
#     #     dlat = radians(site.latitude - lat)
#     #     dlon = radians(site.longitude - lon)
#     #     a = sin(dlat/2)**2 + cos(radians(lat)) * cos(radians(site.latitude)) * sin(dlon/2)**2
#     #     return R * 2 * asin(sqrt(a)) <= site.radius_meters
    
#     def within_radius(self, lat, lon, site):
#         from math import radians, sin, cos, asin, sqrt
#         R = 6371000.0

#         site_lat = float(site.latitude)
#         site_lon = float(site.longitude)

#         dlat = radians(site_lat - float(lat))
#         dlon = radians(site_lon - float(lon))

#         a = sin(dlat / 2) ** 2 + cos(radians(float(lat))) * cos(radians(site_lat)) * sin(dlon / 2) ** 2
#         distance = R * 2 * asin(sqrt(a))

#         return distance <= float(site.distance_meters)


#     def in_shift_window(self, now, shift):
#         start_dt = timezone.make_aware(datetime.combine(now.date(), shift.start_time))
#         end_dt = timezone.make_aware(datetime.combine(now.date(), shift.end_time))
#         grace = timedelta(minutes=30)

#         in_base = start_dt <= now <= end_dt
#         in_grace = (start_dt - grace) <= now <= (end_dt + grace)
#         status_hint = None
#         if not in_base and in_grace:
#             status_hint = "Late Check-in" if now > start_dt else "Early Check-out"
#         return in_base, in_grace, status_hint


class FaceAttendanceView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = FaceUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # --- Face encoding ---
        image_bytes = serializer.validated_data["image"].read()
        uploaded_encoding = get_face_encoding(image_bytes)
        if uploaded_encoding is None:
            return Response({"error": "No face detected"}, status=status.HTTP_400_BAD_REQUEST)

        # --- FAISS Comparison ---
        index_manager = FaceIndexManager.get_instance()
        logger.info(f"FAISS Search: Index contains {index_manager.index.ntotal} faces.")
        matched_id, distance = index_manager.search(uploaded_encoding)

        if not matched_id:
             return Response({"error": "Face not recognized"}, status=status.HTTP_404_NOT_FOUND)
             
        try:
            matched_employee = Employee.objects.get(id=matched_id)
        except Employee.DoesNotExist:
            return Response({"error": "Matched employee not found in DB"}, status=status.HTTP_404_NOT_FOUND)

        # Admin check: Ensure matched employee belongs to the admin's location
        user = getattr(request, "user", None)
        if isinstance(user, User) and user.role == User.Role.ADMIN:
            if matched_employee.location != user.location:
                 return Response({"error": "Face not recognized (Location mismatch)"}, status=status.HTTP_404_NOT_FOUND)

        today = date.today()
        now = timezone.now()

        # --- Attendance rules ---
        # Filter assignments by valid date range for today
        from django.db.models import Q
        assignment = Assignment.objects.filter(
            user_id=matched_employee.id,
            location_id=matched_employee.location_id,
            is_deleted=False
        ).filter(
            # From date is NULL OR from_date <= today
            Q(assignment_from_date__isnull=True) | Q(assignment_from_date__lte=today)
        ).filter(
            # To date is NULL OR to_date >= today
            Q(assignment_to_date__isnull=True) | Q(assignment_to_date__gte=today)
        ).select_related("shift").order_by("-created_on").first()

        user_sites = UserSite.objects.filter(user_id=matched_employee.id).select_related("site")
        location_sites = Site.objects.filter(location_id=matched_employee.location_id)

        shift = None
        if assignment and getattr(assignment, 'shift', None):
            candidate_shift = assignment.shift
            # Check if shift is deleted
            is_deleted = getattr(candidate_shift, 'is_deleted', False)
            if is_deleted:
                # Shift is deleted, don't use it
                shift = None
            else:
                start_time = getattr(candidate_shift, 'start_time', None)
                end_time = getattr(candidate_shift, 'end_time', None)          
                if start_time not in (None, '') and end_time not in (None, ''):
                    shift = candidate_shift
        
        sites = [us.site for us in user_sites] if user_sites.exists() else list(location_sites)

        # --- Geofence check ---
        def _safe_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        lat = _safe_float(request.data.get("latitude"))
        lon = _safe_float(request.data.get("longitude"))
        accuracy = _safe_float(request.data.get("accuracy"))
        address = request.data.get("address")

        nearest_site = None
        nearest_distance = None

        # If no sites are configured for the user/location, skip geofence enforcement.
        if sites:
            if lat is None or lon is None:
                return Response({"error": "Geolocation not provided"}, status=status.HTTP_400_BAD_REQUEST)

            for s in sites:
                dist = self.calculate_distance(lat, lon, s)
                if nearest_distance is None or dist < nearest_distance:
                    nearest_distance = dist
                    nearest_site = s

            # Allow a small buffer equal to reported GPS accuracy (if available) plus 5m slack.
            allowed_radius = float(nearest_site.distance_meters)
            if accuracy is not None:
                allowed_radius += float(accuracy)
            allowed_radius += 5.0

            if nearest_site is None or nearest_distance > allowed_radius:
                return Response(
                    {
                        "error": f"Outside allowed site radius ({round(allowed_radius, 2)} m)",
                        "distance_m": round(nearest_distance, 2) if nearest_distance is not None else None,
                        "allowed_radius_m": round(allowed_radius, 2),
                        "site_id": str(nearest_site.id) if nearest_site else None,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            # If the nearest site has exactly one assigned shift, prefer it over assignment shift
            try:
                assigned = list(nearest_site.shifts.filter(is_deleted=False))
                if len(assigned) == 1:
                    site_shift = assigned[0]
                    if getattr(site_shift, 'start_time', None) not in (None, '') and getattr(site_shift, 'end_time', None) not in (None, ''):
                        shift = site_shift
            except Exception:
                pass
        else:
            # No site configured: allow attendance without geofence
            nearest_site = None
            nearest_distance = None

        # --- Auto checkin/checkout ---
        logs_today = AttendanceLog.objects.filter(employee=matched_employee, timestamp__date=today)
        if shift:
            has_checkin = logs_today.filter(type="checkin", shift=shift).exists()
            has_checkout = logs_today.filter(type="checkout", shift=shift).exists()
        else:
            has_checkin = logs_today.filter(type="checkin").exists()
            has_checkout = logs_today.filter(type="checkout").exists()

        if shift:
            last_log = logs_today.filter(shift=shift).order_by("-timestamp").first()
        else:
            last_log = logs_today.order_by("-timestamp").first()

        if not last_log:
            entry_type = "checkin"
        else:
            entry_type = "checkout" if last_log.type == "checkin" else "checkin"

        # --- Shift timing check ---
        status_label = "Checked-in"
        minutes_late = None
        minutes_early = None
        checkout_delta_min = None
        worked_min = None
        shift_min = None
        diff_min = None

        if shift:
            in_base, in_grace, status_hint = self.in_shift_window(now, shift)

            tz = timezone.get_current_timezone()
            now_local = timezone.localtime(now) if not timezone.is_naive(now) else timezone.make_aware(now, tz)
            start_time = shift.start_time
            end_time = shift.end_time
            if end_time > start_time:
                start_dt_naive = datetime.combine(now_local.date(), start_time)
                end_dt_naive = datetime.combine(now_local.date(), end_time)
            else:
               
                start_dt_naive = datetime.combine(now_local.date(), start_time)
                end_dt_naive = datetime.combine(now_local.date() + timedelta(days=1), end_time)
            start_dt = timezone.make_aware(start_dt_naive, tz)
            end_dt = timezone.make_aware(end_dt_naive, tz)

            # ±1 hour window restriction: Allow attendance 1 hour before shift start to 1 hour after shift end
            # Example: Shift 7am-7pm allows attendance from 6am-8pm
            window_start = start_dt - timedelta(hours=1)
            window_end = end_dt + timedelta(hours=1)
            
            if now_local < window_start or now_local > window_end:
                return Response(
                    {
                        "error": "Attendance not allowed outside shift window",
                        "message": f"You can only mark attendance between {window_start.strftime('%I:%M %p')} and {window_end.strftime('%I:%M %p')}",
                        "shift_time": f"{start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}",
                        "allowed_window": f"{window_start.strftime('%I:%M %p')} - {window_end.strftime('%I:%M %p')}",
                        "current_time": now_local.strftime('%I:%M %p')
                    },
                    status=status.HTTP_403_FORBIDDEN
                )

            logger.info(f"DEBUG: start_time={start_time}, end_time={end_time}, now_local.time()={now_local.time()}")
            logger.info(f"DEBUG: start_dt={start_dt}, end_dt={end_dt}, now_local={now_local}")

            try:
                grace_minutes = int(getattr(shift, "grace_timing", 30) or 30)
            except Exception:
                grace_minutes = 30
            grace = timedelta(minutes=grace_minutes)

            if entry_type == "checkin":
                delta_min = (now_local - start_dt).total_seconds() / 60.0
                logger.info(f"DEBUG CHECKIN: delta_min={delta_min}, now_local={now_local}, start_dt={start_dt}")
                if -15 <= delta_min <= 15:
                    status_label = "On-time Check-in"
                    minutes_late = 0
                elif delta_min < -15:
                    status_label = "Early Check-in"
                    minutes_early = int(round(abs(delta_min)))
                elif delta_min > 15:
                    if delta_min <= 60:
                        status_label = "Late Check-in"
                        minutes_late = int(round(delta_min))
                    else:
                        status_label = "Missed Check-in"
                        minutes_late = int(round(delta_min))


            else:
                delta_end_min = (now_local - end_dt).total_seconds() / 60.0
                checkout_delta_min = int(round(delta_end_min))
                if -15 <= delta_end_min <= 15:
                    status_label = "On-time Check-out"
                elif delta_end_min < -15:
                    status_label = "Early Check-out"
                    checkout_delta_min = int(round(abs(delta_end_min)))
                elif delta_end_min > 15:
                    if delta_end_min <= 60:
                        status_label = "Late Check-out"
                        checkout_delta_min = int(round(delta_end_min))
                    else:
                        status_label = "Missed Checked-out"
                        checkout_delta_min = int(round(delta_end_min))

                if shift:
                    last_checkin = logs_today.filter(type="checkin", shift=shift).order_by("-timestamp").first()
                else:
                    last_checkin = logs_today.filter(type="checkin").order_by("-timestamp").first()
                if last_checkin:
                    checkin_ts = last_checkin.timestamp
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, tz)
                    checkin_time = timezone.localtime(checkin_ts)

                    
                    if checkin_time > now_local:
                        if shift:
                            alt = logs_today.filter(type="checkin", shift=shift, timestamp__lte=now).order_by("-timestamp").first()
                        else:
                            alt = logs_today.filter(type="checkin", timestamp__lte=now).order_by("-timestamp").first()
                        if alt:
                            checkin_ts = alt.timestamp
                            if timezone.is_naive(checkin_ts):
                                checkin_ts = timezone.make_aware(checkin_ts, tz)
                            checkin_time = timezone.localtime(checkin_ts)

                    worked_min = (now_local - checkin_time).total_seconds() / 60.0
                    shift_min = (end_dt - start_dt).total_seconds() / 60.0
                    diff = worked_min - shift_min
                    diff_min = int(round(diff))
                    logger.info(
                        "DEBUG CHECKOUT: checkin_time=%s (tz=%s) now_local=%s (tz=%s)",
                        checkin_time,
                        getattr(checkin_time, "tzinfo", None),
                        now_local,
                        getattr(now_local, "tzinfo", None),
                    )
                    logger.info(
                        "DEBUG CHECKOUT: start_dt=%s end_dt=%s (tz=%s)",
                        start_dt,
                        end_dt,
                        getattr(start_dt, "tzinfo", None),
                    )
                    logger.info(
                        "DEBUG CHECKOUT: worked_min=%.2f shift_min=%.2f diff=%.2f",
                        worked_min,
                        shift_min,
                        diff,
                    )
                    if worked_min < 240:
                        status_label = "Half day Absent"
                    elif diff > 60:
                        status_label = "Overtime"
                    elif diff < -60:
                        status_label = "Undertime"
                if status_hint:
                    status_label = status_hint
        else:
            if entry_type == "checkin":
                status_label = "Check-in"
            else:
                last_checkin = logs_today.filter(type="checkin").order_by("-timestamp").first()
                if not last_checkin:
                    status_label = "Absent"
                else:
                    tz = timezone.get_current_timezone()
                    checkin_ts = last_checkin.timestamp
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, tz)
                    checkin_local = timezone.localtime(checkin_ts)
                    now_local = timezone.localtime(now) if not timezone.is_naive(now) else timezone.make_aware(now, tz)

                    worked_min = (now_local - checkin_local).total_seconds() / 60.0
                 
                    try:
                        worked_min_val = float(worked_min)
                    except Exception:
                        worked_min_val = None

                    if worked_min_val is None:
                        status_label = "Check-out"
                    else:
                        if worked_min_val < 240:
                            status_label = "Absent"
                        elif worked_min_val < 360:
                            status_label = "Half day Absent"
                        elif worked_min_val < 720:
                            status_label = "Early Check-out"
                        elif abs(worked_min_val - 720) < 1.0:
                            status_label = "On-time Check-out"
                        else:
                            status_label = "Delayed Check-out"

        emp_name = matched_employee.name.strip()
        if entry_type == "checkin":
            if status_label == "On-time Check-in":
                message = f"Welcome, {emp_name}! You have checked in on time."
            elif status_label == "Early Check-in":
                if minutes_early is not None:
                    message = f"Welcome, {emp_name}! You have checked in {minutes_early} minutes early."
                else:
                    message = f"Welcome, {emp_name}! You have checked in early."
            elif status_label == "Late Check-in":
                if minutes_late is not None:
                    message = f"Welcome, {emp_name}! You have checked in {minutes_late} minutes late."
                else:
                    message = f"Welcome, {emp_name}! You have checked in late."
            elif status_label == "Missed Check-in":
                if minutes_late is not None:
                    message = f"Welcome, {emp_name}! You have checked in {minutes_late} minutes late; this is considered a missed check-in."
                else:
                    message = f"Welcome, {emp_name}! You have checked in but the check-in time has passed significantly."
            else:
                message = f"Welcome, {emp_name}! Your check-in has been recorded."
        else:  # checkout
            if status_label == "On-time Check-out":
                message = f"Good job today, {emp_name}! You have checked out on time."
            elif status_label == "Early Check-out":
                if checkout_delta_min is not None:
                    message = f"Good job today, {emp_name}! You have checked out {abs(checkout_delta_min)} minutes early."
                elif worked_min is not None:
                    hours_worked = int(worked_min // 60)
                    mins_worked = int(worked_min % 60)
                    message = f"{emp_name}, you have worked {hours_worked} hours {mins_worked} minutes which is less than expected — early check-out."
                else:
                    message = f"Good job today, {emp_name}! You have checked out early."
            elif status_label == "Late Check-out":
                if checkout_delta_min is not None:
                    message = f"Good job today, {emp_name}! You have checked out {checkout_delta_min} minutes late."
                else:
                    message = f"Good job today, {emp_name}! You have checked out late."
            elif status_label == "Missed Checked-out":
                if checkout_delta_min is not None:
                    message = f"Good job today, {emp_name}! You have checked out {abs(checkout_delta_min)} minutes after expected time."
                else:
                    message = f"Good job today, {emp_name}! You have checked out but the check-out time has passed significantly."
            elif status_label == "Half day Absent":
                if worked_min is not None:
                    hours_worked = int(worked_min // 60)
                    mins_worked = int(worked_min % 60)
                    message = f"{emp_name}, you have worked only {hours_worked} hours {mins_worked} minutes, which is less than 6 hours. This will be marked as Half day Absent."
                else:
                    message = f"{emp_name}, you have worked less than 6 hours. This will be marked as Half day Absent."
            elif status_label == "Overtime":
                if diff_min is not None:
                    message = f"Good job today, {emp_name}! You worked {diff_min} minutes overtime."
                else:
                    message = f"Good job today, {emp_name}! You worked overtime."
            elif status_label == "Undertime":
                if diff_min is not None:
                    message = f"Good job today, {emp_name}! You worked {abs(diff_min)} minutes less than the scheduled shift."
                else:
                    message = f"Good job today, {emp_name}! You worked less than the scheduled shift."
            elif status_label == "Absent":
                if worked_min is not None:
                    hours_worked = int(worked_min // 60)
                    mins_worked = int(worked_min % 60)
                    message = f"{emp_name}, you have worked only {hours_worked} hours {mins_worked} minutes which is insufficient. You will be marked Absent."
                else:
                    message = f"{emp_name}, no check-in was found for today. You will be marked Absent."
            elif status_label == "Delayed Check-out":
                if worked_min is not None:
                    hours_worked = int(worked_min // 60)
                    mins_worked = int(worked_min % 60)
                    message = f"Good job today, {emp_name}! You worked {hours_worked} hours {mins_worked} minutes — this is beyond the expected 12 hours and will be marked as Delayed Check-out."
                else:
                    message = f"Good job today, {emp_name}! You have checked out after an extended period."
            else:
                message = f"Good job today, {emp_name}! Your check-out is complete."

        AttendanceLog.objects.create(
            employee=matched_employee,
            type=entry_type,
            timestamp=now,
            site=nearest_site,                  # ✅ assign nearest site
            location=matched_employee.location, # ✅ assign location
            shift=shift,
            latitude=lat,
            longitude=lon,
            address=address,
            # status=status_label (optional if you add field)
        )

        confidence = round(1 - np.sqrt(distance), 2)
        # photo_base64 = base64.b64encode(matched_employee.photo).decode("utf-8") if matched_employee.photo else None

        return Response({
            "status": status_label,
            "message": message,
            "employee": matched_employee.name.strip(),
            "confidence": confidence,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            # "photo": f"data:image/jpeg;base64,{photo_base64}" if photo_base64 else None,
            "attendance_status": status_label,
            "site_id": str(nearest_site.id) if nearest_site else None,
            "location_id": str(matched_employee.location.id),
            "shift_id": str(shift.id) if shift else None,
            }, status=status.HTTP_200_OK)

    # --- Helpers ---
    def calculate_distance(self, lat, lon, site):
        from math import radians, sin, cos, asin, sqrt
        R = 6371000.0
        site_lat = float(site.latitude)
        site_lon = float(site.longitude)

        print("site.latitude", site.latitude)
        print("site.longitude", site.longitude)

        dlat = radians(site_lat - lat)
        dlon = radians(site_lon - lon)
        a = sin(dlat/2)**2 + cos(radians(lat)) * cos(radians(site_lat)) * sin(dlon/2)**2
        return R * 2 * asin(sqrt(a))  # distance in meters

    def in_shift_window(self, now, shift):
        if timezone.is_naive(now):
            now = timezone.make_aware(now, timezone.get_current_timezone())

        now_local = timezone.localtime(now)

        tz = timezone.get_current_timezone()

        start_time = shift.start_time
        end_time = shift.end_time

        if end_time > start_time:
            start_dt_naive = datetime.combine(now_local.date(), start_time)
            end_dt_naive = datetime.combine(now_local.date(), end_time)
        else:
            if now_local.time() >= start_time:
                start_dt_naive = datetime.combine(now_local.date(), start_time)
                end_dt_naive = datetime.combine(now_local.date() + timedelta(days=1), end_time)
            else:
                start_dt_naive = datetime.combine(now_local.date() - timedelta(days=1), start_time)
                end_dt_naive = datetime.combine(now_local.date(), end_time)

        start_dt = timezone.make_aware(start_dt_naive, tz)
        end_dt = timezone.make_aware(end_dt_naive, tz)

        try:
            grace_minutes = int(getattr(shift, "grace_timing", 30) or 30)
        except Exception:
            grace_minutes = 30
        grace = timedelta(minutes=grace_minutes)

        in_base = start_dt <= now_local <= end_dt
        in_grace = (start_dt - grace) <= now_local <= (end_dt + grace)
        status_hint = None

        logger.debug(
            "Shift window check: now=%s start=%s end=%s grace=%s in_base=%s in_grace=%s",
            now_local,
            start_dt,
            end_dt,
            grace,
            in_base,
            in_grace,
        )

        if not in_base and in_grace:
            status_hint = "Late Check-in" if now_local > start_dt else "Early Check-out"
        return in_base, in_grace, status_hint

class RegisterEmployeeView(AuthenticatedAPIView):
    def post(self, request):
        serializer = EmployeeRegisterSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            logger.warning("Invalid registration data: %s", serializer.errors)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        name = serializer.validated_data["name"].strip()
        location = serializer.validated_data["location"]
        face_file = serializer.validated_data["face_image"]
        profile_file = serializer.validated_data.get("profile_photo")

        face_bytes = face_file.read()
        encoding = get_face_encoding(face_bytes)

        if encoding is None:
            logger.info("No face detected during registration")
            return Response({"error": "No face detected"}, status=status.HTTP_400_BAD_REQUEST)

        if request.user.role == User.Role.ADMIN:
            if not request.user.location_id:
                return Response(
                    {"error": "Admin user is not assigned to a location."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if location.id != request.user.location_id:
                return Response(
                    {"error": "Admins can only register employees for their own location."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        profile_bytes = profile_file.read() if profile_file else None

        # Handle payslip_field_config_id
        payslip_field_config = None
        payslip_field_config_id = serializer.validated_data.get('payslip_field_config_id')
        if payslip_field_config_id:
            try:
                from payslip.models import PayslipFieldConfig
                payslip_field_config = PayslipFieldConfig.objects.get(pk=payslip_field_config_id, is_deleted=False)
            except Exception:
                pass  # Ignore if not found or not accessible

        # Create employee with all fields
        employee_data = {
            'name': name,
            'location': location,
            'face_encoding': encoding.tobytes(),
            'photo': profile_bytes or face_bytes,
            'gross_salary': serializer.validated_data.get('gross_salary'),
            'payslip_field_config': payslip_field_config,
            'employee_code': serializer.validated_data.get('employee_code'),
            'department': serializer.validated_data.get('department'),
            'designation': serializer.validated_data.get('designation'),
            'experience_years': serializer.validated_data.get('experience_years'),
            'joining_date': serializer.validated_data.get('joining_date'),
            'bank_account_number': serializer.validated_data.get('bank_account_number'),
            'ifsc_code': serializer.validated_data.get('ifsc_code'),
            'bank_name': serializer.validated_data.get('bank_name'),
            'pan_number': serializer.validated_data.get('pan_number'),
            'aadhaar_number': serializer.validated_data.get('aadhaar_number'),
            'uan_number': serializer.validated_data.get('uan_number'),
            'esi_number': serializer.validated_data.get('esi_number'),
            'email': serializer.validated_data.get('email'),
            'phone': serializer.validated_data.get('phone'),
            'address': serializer.validated_data.get('address'),
        }
        
        # Remove None and empty string values (keep empty strings for some fields like address)
        employee_data = {k: v for k, v in employee_data.items() if v is not None or k in ['address', 'notes']}
        
        employee = Employee.objects.create(**employee_data)
        
        # Update FAISS Index
        if employee.face_encoding:
            FaceIndexManager.get_instance().add_employee(employee.id, employee.face_encoding)
            
        logger.info("Employee registered: %s", name)

        return Response(
            {
            "status": "Employee registered",
            "employee_id": employee.id,
                "name": employee.name,
                "location_id": str(location.id),
            },
            status=status.HTTP_201_CREATED,
        )


def calculate_attendance_summary(employees, start_date, end_date):
    """
    Shared helper function to calculate attendance summary for all employees.
    Handles multiple check-ins/check-outs by pairing sequentially and summing durations.
    
    Returns: List of dictionaries with attendance summary data
    """
    summary = []

    # Generate all dates in range
    all_dates = []
    current_date = start_date
    while current_date <= end_date:
        all_dates.append(current_date)
        current_date += timedelta(days=1)

    from django.db.models import Q
    
    for emp in employees:
        logs = emp.attendancelog_set.filter(
            timestamp__date__range=(start_date, end_date)
        ).select_related('shift', 'site')
        
        # Group logs by date
        logs_by_date = defaultdict(list)
        for log in logs:
            log_date = log.timestamp.date()
            logs_by_date[log_date].append(log)
        
        # Process each date in range (including dates with no logs)
        for log_date in all_dates:
            # Get employee's shift assignment for this specific date
            assignment = Assignment.objects.filter(
                user_id=emp.id,
                location_id=emp.location_id,
                is_deleted=False
            ).filter(
                Q(assignment_from_date__isnull=True) | Q(assignment_from_date__lte=log_date),
                Q(assignment_to_date__isnull=True) | Q(assignment_to_date__gte=log_date),
            ).select_related("shift").order_by("-assignment_from_date").first()

            if assignment:
                # Check if there are multiple overlapping assignments (for warning)
                overlapping_count = Assignment.objects.filter(
                    user_id=emp.id,
                    location_id=emp.location_id,
                    is_deleted=False
                ).filter(
                    Q(assignment_from_date__isnull=True) | Q(assignment_from_date__lte=log_date),
                    Q(assignment_to_date__isnull=True) | Q(assignment_to_date__gte=log_date),
                ).count()
                
                if overlapping_count > 1:
                    # log warning / add note in report
                    overlap_warning = f"{overlapping_count} overlapping assignments on {log_date}"
            
            shift = assignment.shift if assignment else None
            date_logs = logs_by_date.get(log_date, [])
            
            # Separate checkins and checkouts, sorted by timestamp
            checkin_logs = sorted([log for log in date_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
            checkout_logs = sorted([log for log in date_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
            
            # Get earliest checkin and latest checkout for display
            earliest_checkin_log = checkin_logs[0] if checkin_logs else None
            latest_checkout_log = checkout_logs[-1] if checkout_logs else None
            
            # For display purposes (return in response)
            checkin_time = earliest_checkin_log.timestamp if earliest_checkin_log else None
            checkout_time = latest_checkout_log.timestamp if latest_checkout_log else None
            
            # Track multiple entries info
            checkin_count = len(checkin_logs)
            checkout_count = len(checkout_logs)
            has_multiple_entries = (checkin_count > 1) or (checkout_count > 1)
            
            # Calculate total duration by pairing checkins with checkouts sequentially
            total_worked_seconds = 0
            paired_count = 0
            pair_details = []  # Store details of each valid pair
            
            # Pair checkins with checkouts sequentially
            min_pairs = min(len(checkin_logs), len(checkout_logs))
            
            for i in range(min_pairs):
                checkin_log = checkin_logs[i]
                checkout_log = checkout_logs[i]
                
                # Ensure checkout comes after checkin (valid pair)
                if checkout_log.timestamp > checkin_log.timestamp:
                    # Make timezone-aware
                    tz = timezone.get_current_timezone()
                    checkin_ts = checkin_log.timestamp
                    checkout_ts = checkout_log.timestamp
                    
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, tz)
                    if timezone.is_naive(checkout_ts):
                        checkout_ts = timezone.make_aware(checkout_ts, tz)
                    
                    # Calculate duration for this pair
                    pair_duration = (checkout_ts - checkin_ts).total_seconds()
                    
                    # Only add positive durations (safety check)
                    if pair_duration > 0:
                        total_worked_seconds += pair_duration
                        paired_count += 1
                        
                        # Store pair details for frontend
                        hours = int(pair_duration // 3600)
                        minutes = int((pair_duration % 3600) // 60)
                        pair_details.append({
                            "pair_number": paired_count,
                            "checkin": checkin_log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            "checkout": checkout_log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            "duration": f"{hours:02d}:{minutes:02d}",
                            "duration_seconds": int(pair_duration)
                        })
            
            # Format total duration
            duration_str = None
            worked_seconds = None
            if total_worked_seconds > 0:
                worked_seconds = total_worked_seconds
                hours = int(worked_seconds // 3600)
                minutes = int((worked_seconds % 3600) // 60)
                duration_str = f"{hours:02d}:{minutes:02d}"
            else:
                worked_seconds = None
                duration_str = "—"
            
            # Build explanation note for multiple entries
            multiple_entries_note = None

            # Determine effective shift for this day: prefer site-specific shift from logs, else assignment shift
            effective_shift = None
            site_from_log = None

            if has_multiple_entries:
                if checkin_count > 1 and checkout_count > 1:
                    if checkin_count == checkout_count:
                        multiple_entries_note = f"Multiple entries: {checkin_count} check-ins and {checkout_count} check-outs. Duration calculated from {paired_count} valid pair(s)."
                    else:
                        unmatched = abs(checkin_count - checkout_count)
                        multiple_entries_note = f"Multiple entries: {checkin_count} check-ins and {checkout_count} check-outs ({unmatched} unmatched). Duration calculated from {paired_count} valid pair(s)."
                elif checkin_count > 1:
                    multiple_entries_note = f"Multiple check-ins ({checkin_count} total). Duration calculated from {paired_count} valid pair(s) with available check-outs."
                elif checkout_count > 1:
                    multiple_entries_note = f"Multiple check-outs ({checkout_count} total). Duration calculated from {paired_count} valid pair(s) with available check-ins."

            # Prefer earliest checkin site's shift, fallback to latest checkout site's shift
            if earliest_checkin_log and getattr(earliest_checkin_log, 'site', None):
                site_from_log = getattr(earliest_checkin_log, 'site')
            elif latest_checkout_log and getattr(latest_checkout_log, 'site', None):
                site_from_log = getattr(latest_checkout_log, 'site')
            
            # Get shift from site if available (Site has ManyToMany with Shift)
            if site_from_log:
                try:
                    assigned_shifts = list(site_from_log.shifts.filter(is_deleted=False))
                    if len(assigned_shifts) == 1:
                        s = assigned_shifts[0]
                        if getattr(s, 'start_time', None) not in (None, '') and getattr(s, 'end_time', None) not in (None, ''):
                            effective_shift = s
                except Exception:
                    pass

            # Fallback to assignment shift
            if effective_shift is None:
                effective_shift = shift
            
            # Make times timezone-aware for consistent calculations
            tz = timezone.get_current_timezone()
            if checkin_time and timezone.is_naive(checkin_time):
                checkin_time = timezone.make_aware(checkin_time, tz)
            if checkout_time and timezone.is_naive(checkout_time):
                checkout_time = timezone.make_aware(checkout_time, tz)
            
            # If no checkin on this day, mark as Absent
            if not checkin_time:
                summary.append({
                    "date": log_date.strftime("%Y-%m-%d"),
                    "name": emp.name,
                    "department": emp.department or "—",
                    "location": emp.location.name if emp.location else "—",
                    "shift": effective_shift.shift_name if effective_shift else (shift.shift_name if shift else "—"),
                    "shift_start": effective_shift.start_time.strftime("%H:%M") if effective_shift else (shift.start_time.strftime("%H:%M") if shift else "—"),
                    "shift_end": effective_shift.end_time.strftime("%H:%M") if effective_shift else (shift.end_time.strftime("%H:%M") if shift else "—"),
                    "checkin": "—",
                    "checkout": "—",
                    "duration": "—",
                    "status": "Absent",
                    "variance": "—",
                    "remarks": "Absent",
                    "note": "No check-in",
                    "has_multiple_entries": False,
                    "checkin_count": 0,
                    "checkout_count": 0,
                    "valid_pairs_count": 0,
                    "pair_details": [],
                    "multiple_entries_note": None,
                })
                continue

            if checkin_time and not checkout_time:
                summary.append({
                    "date": log_date.strftime("%Y-%m-%d"),
                    "name": emp.name,
                    "department": emp.department or "—",
                    "location": emp.location.name if emp.location else "—",
                    "shift": effective_shift.shift_name if effective_shift else (shift.shift_name if shift else "—"),
                    "shift_start": effective_shift.start_time.strftime("%H:%M") if effective_shift else (shift.start_time.strftime("%H:%M") if shift else "—"),
                    "shift_end": effective_shift.end_time.strftime("%H:%M") if effective_shift else (shift.end_time.strftime("%H:%M") if shift else "—"),
                    "checkin": checkin_time.strftime("%Y-%m-%d %H:%M:%S") if checkin_time else "—",
                    "checkout": "—",
                    "duration": "—",
                    "status": "Absent",
                    "variance": "—",
                    "remarks": "No checkout",
                    "note": "Only check-in, no checkout",
                    "has_multiple_entries": has_multiple_entries,
                    "checkin_count": checkin_count,
                    "checkout_count": checkout_count,
                    "valid_pairs_count": paired_count,
                    "pair_details": pair_details,
                    "multiple_entries_note": multiple_entries_note,
                })
                continue
            
            # Compute variance and remarks
            variance_str = "—"
            remarks = "—"
            note = "—"
            status_str = "Present"  # Default to Present, will be overridden by duration logic below
            
            if shift and checkin_time:
                shift_start_time = effective_shift.start_time if effective_shift else None
                shift_end_time = effective_shift.end_time if effective_shift else None
                
                if shift_start_time is None or shift_end_time is None:
                    # No valid shift times, skip variance calculation
                    summary.append({
                        "date": log_date.strftime("%Y-%m-%d"),
                        "name": emp.name,
                        "department": emp.department or "—",
                        "location": emp.location.name if emp.location else "—",
                        "shift": effective_shift.shift_name if effective_shift else (shift.shift_name if shift else "—"),
                        "shift_start": "—",
                        "shift_end": "—",
                        "checkin": checkin_time.strftime("%Y-%m-%d %H:%M:%S") if checkin_time else "—",
                        "checkout": checkout_time.strftime("%Y-%m-%d %H:%M:%S") if checkout_time else "—",
                        "duration": duration_str or "—",
                        "status": status_str,
                        "variance": variance_str,
                        "remarks": remarks,
                        "note": "No valid shift times",
                        "has_multiple_entries": has_multiple_entries,
                        "checkin_count": checkin_count,
                        "checkout_count": checkout_count,
                        "valid_pairs_count": paired_count,
                        "pair_details": pair_details,
                        "multiple_entries_note": multiple_entries_note,
                    })
                    continue
                
                # Create aware datetime objects for today
                shift_start_dt = timezone.make_aware(
                    datetime.combine(log_date, shift_start_time),
                    tz
                )
                shift_end_dt = timezone.make_aware(
                    datetime.combine(log_date, shift_end_time),
                    tz
                )
                
                # Handle night shifts (end_time < start_time)
                if shift_end_time < shift_start_time:
                    shift_end_dt = timezone.make_aware(
                        datetime.combine(log_date + timedelta(days=1), shift_end_time),
                        tz
                    )
                
                shift_duration_seconds = (shift_end_dt - shift_start_dt).total_seconds()
                
                # Checkin variance (minutes before/after shift start)
                checkin_variance_seconds = (checkin_time - shift_start_dt).total_seconds()
                checkin_variance_minutes = int(checkin_variance_seconds / 60)
                
                # Determine checkin remarks
                if -15 <= checkin_variance_minutes <= 15:
                    remarks = "On-time Check-in"
                elif checkin_variance_minutes < -15:
                    remarks = f"Early Check-in"
                    note = f"{abs(checkin_variance_minutes)}min early"
                elif checkin_variance_minutes > 15:
                    if checkin_variance_minutes <= 60:
                        remarks = "Late Check-in"
                        note = f"{checkin_variance_minutes}min late"
                    else:
                        remarks = "Missed Check-in"
                        note = f">{60}min late"
                
                # Checkout variance (if checkout exists)
                if checkout_time:
                    checkout_variance_seconds = (shift_end_dt - checkout_time).total_seconds()
                    checkout_variance_minutes = int(checkout_variance_seconds / 60)
                    
                    # Determine checkout remarks (overrides checkin if checkout is later)
                    if -15 <= checkout_variance_minutes <= 15:
                        remarks = "On-time Check-out"
                    elif checkout_variance_minutes < -15:
                        remarks = f"Late Check-out"
                        note = f"{abs(checkout_variance_minutes)}min late"
                    elif checkout_variance_minutes > 15:
                        remarks = "Early Check-out"
                        note = f"{checkout_variance_minutes}min early"
                    
                    # Compute variance as worked - shift duration
                    if worked_seconds is not None:
                        variance_seconds = worked_seconds - shift_duration_seconds
                        variance_hours = int(abs(variance_seconds) // 3600)
                        variance_mins = int((abs(variance_seconds) % 3600) // 60)
                        sign = '-' if variance_seconds < 0 else ''
                        variance_str = f"{sign}{variance_hours:02d}:{variance_mins:02d}"

                        # --- Duration-based Status Logic ---
                        wh = int(worked_seconds // 3600)
                        wm = int((worked_seconds % 3600) // 60)
                        
                        if worked_seconds < 3 * 3600:
                            status_str = "Absent"
                            remarks = "Absent"
                            note = f"Worked {wh}h {wm}m (<3h)"
                        elif worked_seconds < 6 * 3600:
                            status_str = "Half day Present"
                            remarks = "Half day Present"
                            note = f"Worked {wh}h {wm}m (3h-6h)"
                        else:
                            status_str = "Present"
                            # Keep existing note if it was set by variance logic, 
                            # or update it with variance details
                            if note == "—":
                                if abs(variance_seconds) <= 15 * 60:
                                    note = "0 to +/- 15min"
                                elif abs(variance_seconds) <= 60 * 60:
                                    note = f">15min & <60min"
                                elif variance_seconds > 0:
                                    note = f">+1hr (Overtime)"
                                else:
                                    note = f"<-1hr (Undertime)"
                else:
                    # No checkout, just use checkin variance
                    variance_str = "—"
            
            summary.append({
                "date": log_date.strftime("%Y-%m-%d"),
                "name": emp.name,
                "department": emp.department or "—",
                "location": emp.location.name if emp.location else "—",
                "shift": effective_shift.shift_name if effective_shift else (shift.shift_name if shift else "—"),
                "shift_start": effective_shift.start_time.strftime("%H:%M") if effective_shift else (shift.start_time.strftime("%H:%M") if shift else "—"),
                "shift_end": effective_shift.end_time.strftime("%H:%M") if effective_shift else (shift.end_time.strftime("%H:%M") if shift else "—"),
                "checkin": checkin_time.strftime("%Y-%m-%d %H:%M:%S") if checkin_time else "—",
                "checkout": checkout_time.strftime("%Y-%m-%d %H:%M:%S") if checkout_time else "—",
                "duration": duration_str or "—",
                "status": status_str,
                "variance": variance_str,
                "remarks": remarks,
                "note": note,
                "has_multiple_entries": has_multiple_entries,
                "checkin_count": checkin_count,
                "checkout_count": checkout_count,
                "valid_pairs_count": paired_count,
                "pair_details": pair_details,
                "multiple_entries_note": multiple_entries_note,
            })
    
    return summary


class AttendanceSummaryView(AuthenticatedAPIView):
    def get(self, request):
        today = timezone.localtime().date()
        logger.info(f"Attendance summary requested. Current timezone date: {today}, timezone.now(): {timezone.localtime()}")  # Use timezone-aware date
        start_date = request.query_params.get('start_date', today.strftime('%Y-%m-%d'))
        end_date = request.query_params.get('end_date', today.strftime('%Y-%m-%d'))
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            start_date = today
            end_date = today
        
        # Get employees
        employees = Employee.objects.select_related("location").prefetch_related("attendancelog_set")
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)

        # Use shared helper function
        summary = calculate_attendance_summary(employees, start_date, end_date)
        
        return Response(summary)


class AttendanceSummaryExportView(AuthenticatedAPIView):
    def get(self, request):
        today = timezone.localtime().date()  # Use timezone-aware date
        logger.info(f"Attendance export requested. Current timezone date: {today}, timezone.now(): {timezone.localtime()}")
        start_date = request.query_params.get('start_date', today.strftime('%Y-%m-%d'))
        end_date = request.query_params.get('end_date', today.strftime('%Y-%m-%d'))
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            start_date = today
            end_date = today
        
        # Get employees
        employees = Employee.objects.select_related("location").prefetch_related("attendancelog_set")
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)
        
        # Use shared helper function
        summary = calculate_attendance_summary(employees, start_date, end_date)
        
        # Convert to Excel
        wb = Workbook()
        ws = wb.active
        ws.title = "Attendance Summary"
        
        # Headers - include new fields for multiple entries
        ws.append([
            "Date", "Location", "Name", "Department", "Shift", "Shift Start", "Shift End",
            "Check-in", "Check-out", "Duration", "Status", "Variance", "Remarks", "Note",
            "PunchRecords",  # New column for all check-in/checkout details
            "Has Multiple Entries", "Check-in Count", "Check-out Count",
        ])
        #"Valid Pairs","Multiple Entries Note"
        # Add data rows
        for row_data in summary:
            # Build PunchRecords column - all check-in/checkout sessions with durations
            punch_records = self._build_punch_records(row_data)
            
            ws.append([
                row_data["date"],
                row_data["location"],
                row_data["name"],
                row_data["department"],
                row_data["shift"],
                row_data["shift_start"],
                row_data["shift_end"],
                row_data["checkin"],
                row_data["checkout"],
                row_data["duration"],
                row_data["status"],
                row_data["variance"],
                row_data["remarks"],
                row_data["note"],
                punch_records,  # New PunchRecords column
                "Yes" if row_data["has_multiple_entries"] else "No",
                row_data["checkin_count"],
                row_data["checkout_count"],
                # row_data["valid_pairs_count"],
                # row_data["multiple_entries_note"] or "",
            ])
        
        # Generate filename with date range
        filename = f"attendance_summary_{start_date}_{end_date}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)
        
        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        return Response({"file_url": file_url})
    
    def _build_punch_records(self, row_data):
        """
        Build a user-friendly string showing all check-in/checkout sessions with durations.
        Format: "08:00 AM - 12:00 PM (4h 0m) | 01:00 PM - 06:00 PM (5h 0m)"
        """
        employee_name = row_data.get("name")
        date_str = row_data.get("date")
        
        if not employee_name or not date_str:
            return ""
        
        try:
            # Parse date
            check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Get employee
            employee = Employee.objects.filter(name=employee_name).first()
            if not employee:
                return ""
            
            # Get all logs for this employee on this date
            logs = AttendanceLog.objects.filter(
                employee=employee,
                timestamp__date=check_date
            ).order_by('timestamp')
            
            if not logs.exists():
                return ""
            
            # Build sessions
            sessions = []
            checkin_time = None
            
            for log in logs:
                if log.type == 'checkin':
                    checkin_time = log.timestamp
                elif log.type == 'checkout' and checkin_time:
                    # Calculate duration
                    duration = log.timestamp - checkin_time
                    hours = int(duration.total_seconds() // 3600)
                    minutes = int((duration.total_seconds() % 3600) // 60)
                    
                    # Format session
                    session_str = f"{checkin_time.strftime('%I:%M %p')} - {log.timestamp.strftime('%I:%M %p')} ({hours}h {minutes}m)"
                    sessions.append(session_str)
                    checkin_time = None
            
            # Handle unpaired check-in (no checkout)
            if checkin_time:
                sessions.append(f"{checkin_time.strftime('%I:%M %p')} - (No checkout)")
            
            # Join all sessions
            return " | ".join(sessions) if sessions else ""
            
        except Exception as e:
            logger.error(f"Error building punch records: {e}")
            return ""
        
        # Save file
        filename = f"attendance_summary_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        # file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename).replace("http://", "https://")
        return Response({"file_url": file_url})


class MonthlyAttendanceStatusView(AuthenticatedAPIView):
    def get(self, request):
        month = request.query_params.get("month")
        if not month:
            return Response(
                {"error": "Month is required in YYYY-MM format"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            year, month_num = map(int, month.split("-"))
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except Exception:
            return Response(
                {"error": "Invalid month format"}, status=status.HTTP_400_BAD_REQUEST
            )

        date_range = [
            start_date + timedelta(days=i)
            for i in range((end_date - start_date).days + 1)
        ]
        employees = Employee.objects.select_related("location").all()
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)

        logs = AttendanceLog.objects.filter(
            timestamp__date__range=(start_date, end_date)
        )
        if request.user.role == User.Role.ADMIN:
            logs = logs.filter(employee__location=request.user.location)

        logs_by_key = defaultdict(list)
        for log in logs:
            try:
                log_date = log.timestamp.date()
            except Exception:
                continue
            logs_by_key[(log.employee_id, log_date)].append(log)

        attendance_map = defaultdict(dict)
        for (emp_id, log_date), day_logs in logs_by_key.items():
            # Determine presence if any checkin or checkout exists
            types = {l.type for l in day_logs}
            if 'checkin' in types or 'checkout' in types:
                checkins = [l.timestamp for l in day_logs if l.type == 'checkin']
                checkouts = [l.timestamp for l in day_logs if l.type == 'checkout']

                # If there is a check-in but no checkout (or vice versa), mark Absent
                if (checkins and not checkouts) or (checkouts and not checkins):
                    attendance_map[emp_id][log_date] = 'A'
                    continue

                worked_seconds = None
                if checkins and checkouts:
                    # use earliest checkin and latest checkout
                    try:
                        start_ts = min(checkins)
                        end_ts = max(checkouts)
                        if timezone.is_naive(start_ts):
                            start_ts = timezone.make_aware(start_ts, timezone.get_current_timezone())
                        if timezone.is_naive(end_ts):
                            end_ts = timezone.make_aware(end_ts, timezone.get_current_timezone())
                        worked_seconds = (end_ts - start_ts).total_seconds()
                    except Exception:
                        worked_seconds = None

                # Mark as Half-day Absent if worked < 4 hours
                if worked_seconds is not None and worked_seconds < 4 * 3600:
                    attendance_map[emp_id][log_date] = 'HA'
                else:
                    attendance_map[emp_id][log_date] = 'P'
            else:
                attendance_map[emp_id][log_date] = 'A'

        summary = []
        for emp in employees:
            row = {"name": emp.name}
            for day in date_range:
                if emp.id in attendance_map and day in attendance_map[emp.id]:
                    status_code = attendance_map[emp.id][day]
                else:
                    has_any = any(k[0] == emp.id for k in logs_by_key.keys())
                    status_code = "A" if has_any else "-"
                row[day.strftime("%d-%b")] = status_code
            summary.append(row)

        return Response(summary)


class MonthlyAttendanceStatusExportView(AuthenticatedAPIView):
    def get(self, request):
        month = request.query_params.get("month")
        if not month:
            return Response({"error": "Month is required in YYYY-MM format"}, status=400)

        try:
            year, month_num = map(int, month.split("-"))
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except Exception:
            return Response({"error": "Invalid month format"}, status=400)

        date_range = [
            start_date + timedelta(days=i)
            for i in range((end_date - start_date).days + 1)
        ]
        employees = Employee.objects.select_related("location").all()
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)

        logs = AttendanceLog.objects.filter(
            timestamp__date__range=(start_date, end_date)
        )
        if request.user.role == User.Role.ADMIN:
            logs = logs.filter(employee__location=request.user.location)

        attendance_map = {}
        for log in logs:
            key = (log.employee_id, log.timestamp.date())
            attendance_map[key] = "P"

        wb = Workbook()
        ws = wb.active
        ws.title = f"Attendance {month}"

        header = ["Name"] + [d.strftime("%d-%b") for d in date_range] + [
            "Present",
            "Absent",
        ]
        ws.append(header)

        for emp in employees:
            row = [emp.name]
            present_count = 0
            absent_count = 0

            for d in date_range:
                key = (emp.id, d)
                status_code = attendance_map.get(
                    key,
                    "A" if any(k[0] == emp.id for k in attendance_map) else "-",
                )
                row.append(status_code)
                if status_code == "P":
                    present_count += 1
                elif status_code == "A":
                    absent_count += 1

            row.append(present_count)
            row.append(absent_count)
            ws.append(row)

        filename = f"monthly_attendance_{month}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        return Response({"file_url": file_url})


class GeneratePayrollView(AuthenticatedAPIView):
    def post(self, request):
        month = request.data.get("month")
        if not month:
            return Response({"error": "Month is required"}, status=400)

        year, month_num = map(int, month.split("-"))
        start_date = datetime(year, month_num, 1).date()
        end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()

        employees = Employee.objects.all()
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)

        logs = AttendanceLog.objects.filter(timestamp__date__range=(start_date, end_date))
        if request.user.role == User.Role.ADMIN:
            logs = logs.filter(employee__location=request.user.location)

        attendance_map = {}
        for log in logs:
            key = (log.employee_id, log.timestamp.date())
            attendance_map[key] = "P"

        for emp in employees:
            present = sum(
                1
                for day in range((end_date - start_date).days + 1)
                if attendance_map.get((emp.id, start_date + timedelta(days=day))) == "P"
            )
            absent = sum(
                1
                for day in range((end_date - start_date).days + 1)
                if attendance_map.get((emp.id, start_date + timedelta(days=day))) == "A"
            )

            base_salary = emp.base_salary or 0
            deduction_per_day = emp.deduction_per_day or 0
            deductions = absent * deduction_per_day
            pf_deduction = (base_salary * Decimal("0.12")).quantize(Decimal("0.01"))
            esi_deduction = (base_salary * Decimal("0.0175")).quantize(
                Decimal("0.01")
            )
            net_pay = base_salary - deductions - pf_deduction - esi_deduction

            PayrollRecord.objects.create(
                employee=emp,
                month=month,
                present_days=present,
                absent_days=absent,
                base_salary=base_salary,
                deduction_per_day=deduction_per_day,
                deductions=deductions,
                pf_deduction=pf_deduction,
                esi_deduction=esi_deduction,
                net_pay=net_pay,
            )

        return Response({"status": f"Payroll generated for {month}"})


class PayrollExportView(AuthenticatedAPIView):
    def get(self, request):
        month = request.query_params.get("month")
        if not month:
            return Response({"error": "Month is required in YYYY-MM format"}, status=400)

        records = PayrollRecord.objects.filter(month=month)
        if request.user.role == User.Role.ADMIN:
            records = records.filter(employee__location=request.user.location)

        if not records.exists():
            return Response(
                {"error": "No payroll records found for this month"}, status=404
            )

        wb = Workbook()
        ws = wb.active
        ws.title = f"Payroll {month}"

        ws.append(
            [
                "Employee",
                "Present Days",
                "Absent Days",
                "Base Salary",
                "Deduction/Day",
                "Deductions",
                "PF",
                "ESI",
                "Net Pay",
            ]
        )

        for record in records:
            ws.append(
                [
                record.employee.name,
                record.present_days,
                record.absent_days,
                float(record.base_salary),
                float(record.deduction_per_day),
                float(record.deductions),
                float(record.pf_deduction),
                float(record.esi_deduction),
                float(record.net_pay),
                ]
            )

        filename = f"payroll_{month}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        return Response({"file_url": file_url})