import base64
import logging
import os
import time
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

import face_recognition
import numpy as np
from django.conf import settings
from django.db import connection
from django.db.models import Min, Max
from django.utils import timezone
from django.utils.timezone import localtime
from openpyxl import Workbook
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
import pytz
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


def get_timezone_from_location(location):
    """Get timezone from location by finding an admin user for that location
    
    Args:
        location: Location instance or None
    
    Returns:
        pytz timezone object
    """
    if location:
        # Find an admin user for this location
        admin_user = User.objects.filter(
            location=location,
            role=User.Role.ADMIN,
            is_deleted=False,
            is_active=True
        ).first()
        
        if admin_user:
            admin_timezone = getattr(admin_user, 'timezone', None)
            if admin_timezone:
                try:
                    return pytz.timezone(admin_timezone)
                except pytz.exceptions.UnknownTimeZoneError:
                    logger.warning(f"Unknown timezone '{admin_timezone}' for admin {admin_user.email} at location {location.name}, using default")
    # Default to Asia/Kolkata if no location or no admin found
    return pytz.timezone('Asia/Kolkata')


def get_user_timezone(user, location=None):
    """Get user's timezone, or location's admin timezone, or default to Asia/Kolkata
    
    Args:
        user: User instance or AnonymousUser or None
        location: Location instance (used when user is AnonymousUser to find admin timezone)
    
    Returns:
        pytz timezone object
    """
    # Check if user is authenticated and is a User instance (not AnonymousUser)
    if user and isinstance(user, User):
        user_timezone = getattr(user, 'timezone', None)
        if user_timezone:
            try:
                return pytz.timezone(user_timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                user_email = getattr(user, 'email', 'unknown')
                logger.warning(f"Unknown timezone '{user_timezone}' for user {user_email}, using default")
    
    # For AnonymousUser or None, try to get timezone from location's admin
    if location:
        return get_timezone_from_location(location)
    
    # Default to Asia/Kolkata
    return pytz.timezone('Asia/Kolkata')


def get_user_local_date(user, utc_datetime=None, location=None):
    """Get today's date in user's timezone or location's admin timezone
    
    Args:
        user: User instance or AnonymousUser or None
        utc_datetime: UTC datetime (defaults to now)
        location: Location instance (used when user is AnonymousUser)
    
    Returns:
        date object in user's/location's timezone
    """
    if utc_datetime is None:
        utc_datetime = timezone.now()
    user_tz = get_user_timezone(user, location=location)
    # Convert UTC datetime to user's/location's timezone
    if timezone.is_naive(utc_datetime):
        utc_datetime = timezone.make_aware(utc_datetime, pytz.UTC)
    local_datetime = utc_datetime.astimezone(user_tz)
    return local_datetime.date()


class AuthenticatedAPIView(APIView):
    authentication_classes = [SimpleTokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]


class TimezoneListView(APIView):
    """API endpoint to get all available timezones"""
    
    def get(self, request):
        """Return list of all available timezones from pytz"""
        timezones = [
            {"value": tz, "label": tz}
            for tz in pytz.all_timezones
        ]
        return Response(timezones)


class LocationListCreateView(AuthenticatedAPIView):
    def get(self, request):
        max_retries = 2
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                # Ensure database connection is alive
                connection.ensure_connection()
                
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
                
            except Exception as e:
                error_msg = str(e)
                is_connection_error = 'Lost connection' in error_msg or '2013' in error_msg or 'OperationalError' in str(type(e).__name__)
                
                if is_connection_error and retry_count < max_retries:
                    retry_count += 1
                    logger.warning(f"Database connection error in LocationListCreateView (attempt {retry_count}/{max_retries}): {error_msg}")
                    # Close the broken connection
                    try:
                        connection.close()
                    except:
                        pass
                    # Wait a bit before retrying
                    time.sleep(0.5)
                    continue
                else:
                    logger.error(f"Error in LocationListCreateView: {error_msg}", exc_info=True)
                    if is_connection_error:
                        return Response(
                            {"detail": "Database connection error. Please try again."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE
                        )
                    return Response(
                        {"detail": f"An error occurred: {error_msg}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
        
        # If we exhausted retries
        return Response(
            {"detail": "Database connection error after multiple retries. Please try again later."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )

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
            # Filter by shift's location field directly
            shifts = shifts.filter(location_id=location_id)

        shifts = shifts.order_by("shift_name").distinct()

        serializer = ShiftSerializer(shifts, many=True)
        return Response(serializer.data)

    def post(self, request):
        """
        Create one or many shifts.

        - Single shift: POST /api/shifts/ with a JSON object
        - Bulk shifts:  POST /api/shifts/ with a JSON array of objects
        
        For admin users: If location_id is not provided, automatically set it to the admin's location.
        """
        data = request.data
        many = isinstance(data, list)
        
        # Auto-set location_id for admin users if not provided
        if request.user.role == User.Role.ADMIN and request.user.location_id:
            if many:
                # Bulk creation: add location_id to each shift if not present
                for shift_data in data:
                    if 'location_id' not in shift_data or not shift_data.get('location_id'):
                        shift_data['location_id'] = str(request.user.location_id)
            else:
                # Single shift: add location_id if not present
                if 'location_id' not in data or not data.get('location_id'):
                    data = data.copy()
                    data['location_id'] = str(request.user.location_id)

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


def get_location_admin(employee):
    return User.objects.filter(
        location_id=employee.location_id,
        role=User.Role.ADMIN,
        is_active=True,
        is_deleted=False
    ).first()

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
        # user =get_location_admin(matched_employee)

        if isinstance(user, User) and user.role == User.Role.ADMIN:
            if matched_employee.location != user.location:
                 return Response({"error": "Face not recognized (Location mismatch)"}, status=status.HTTP_404_NOT_FOUND)

        # Get timezone and calculate today's date
        # For unauthenticated requests (Face_AI_Frontend), use employee's location admin timezone
        # For authenticated users, use their timezone
        now = timezone.now()  # UTC
        employee_location = matched_employee.location if matched_employee else None
        user_tz = get_user_timezone(user, location=employee_location)  # Uses location admin timezone if AnonymousUser
        today = get_user_local_date(user, now, location=employee_location)  # Uses location admin timezone if AnonymousUser
        now_local = now.astimezone(user_tz) if not timezone.is_naive(now) else timezone.make_aware(now, pytz.UTC).astimezone(user_tz)

        # Extra high-signal operational logging to confirm timezone correctness in production
        # (Journald / gunicorn logs)
        try:
            tz_name = getattr(user_tz, "zone", str(user_tz))
        except Exception:
            tz_name = str(user_tz)
        logger.info(
            "ATTN STEP TZ-1: resolved user_tz=%s now_utc=%s now_local=%s",
            tz_name,
            now,
            now_local,
        )
        
        # Safe logging - handle AnonymousUser
        user_email = None
        user_timezone_str = 'Asia/Kolkata'
        if user and isinstance(user, User):
            user_email = getattr(user, 'email', None)
            user_timezone_str = getattr(user, 'timezone', 'Asia/Kolkata')
        elif user and hasattr(user, 'is_authenticated') and not user.is_authenticated:
            user_email = 'AnonymousUser'
            # Get timezone from location's admin
            if employee_location:
                admin_user = User.objects.filter(
                    location=employee_location,
                    role=User.Role.ADMIN,
                    is_deleted=False,
                    is_active=True
                ).first()
                if admin_user:
                    user_timezone_str = getattr(admin_user, 'timezone', 'Asia/Kolkata')
                    logger.info(f"DEBUG: Using admin timezone for location {employee_location.name}: {user_timezone_str} (from admin {admin_user.email})")
                else:
                    user_timezone_str = f"Location:{employee_location.name} (no admin, default)"
            else:
                user_timezone_str = 'No location (default)'
        
        logger.info(
            f"DEBUG: user={user_email}, employee_location={employee_location.name if employee_location else None}, timezone={user_timezone_str}, today={today}, now_utc={now}, now_local={now_local}"
        )
        # --- Attendance rules ---
        from django.db.models import Q

        user_sites = UserSite.objects.filter(user_id=matched_employee.id, is_deleted=False).select_related("site")
        location_sites = Site.objects.filter(location_id=matched_employee.location_id, is_deleted=False)
        
        # Log the sites assigned to this employee
        assigned_site_ids = [us.site.id for us in user_sites]
        logger.info(f"DEBUG: Employee {matched_employee.id} has {len(assigned_site_ids)} assigned sites: {assigned_site_ids}")
        
        shift = None
        # Use only explicitly assigned sites, or all location sites if none are assigned
        sites = [us.site for us in user_sites] if user_sites.exists() else list(location_sites)
        logger.info(f"DEBUG: Will use {len(sites)} sites for geofence check (from {'UserSite assignments' if user_sites.exists() else 'location_sites'})")

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
            # ✅ PRIORITY 1: Use employee's ASSIGNED shift from Assignment table
            # If present, we use it regardless of site M2M assignments
            logger.info(f"DEBUG: Finding shifts for employee: {matched_employee.id}")
            try:
                # Step 1: Get employee's shift assignment
                assignment = Assignment.objects.filter(
                    user_id=matched_employee.id,
                    location_id=matched_employee.location_id,
                    is_deleted=False
                ).order_by('-assignment_from_date').first()
                
                if assignment and assignment.shift:
                    shift = assignment.shift
                    logger.info(f"DEBUG: ✅ SELECTED assigned shift (Priority 1): {shift.shift_name} ({shift.start_time} - {shift.end_time}) [id={shift.id}]")
                elif nearest_site:
                    # PRIORITY 2: Fallback to site's assigned shifts if no direct assignment
                    site_shifts = nearest_site.shifts.filter(is_deleted=False)
                    if site_shifts.exists():
                        # Try to find a shift where the user is currently in window
                        active_shift = None
                        for s in site_shifts:
                            in_base, in_grace, _ = self.in_shift_window(now, s, tz=user_tz)
                            if in_base or in_grace:
                                active_shift = s
                                break
                        
                        shift = active_shift or site_shifts.first()
                        if shift:
                            logger.info(f"DEBUG: ✅ SELECTED site shift (Priority 2): {shift.shift_name} ({shift.start_time} - {shift.end_time}) [id={shift.id}]")
                
                if not shift:
                    logger.info(f"DEBUG: ⚠️ No shift found for employee {matched_employee.id} (Assignment or Site) - allowing check-in/out at any time")
            except Exception as e:
                logger.exception(f"ERROR fetching shift: {e}")
                shift = None
        else:
            # No site configured: allow attendance without geofence
            nearest_site = None
            nearest_distance = None

        # --- Auto checkin/checkout ---
        # Filter logs by date in user's timezone
        # Since timestamps are stored in UTC, we need to filter by date range that covers the user's local day
        # IMPORTANT: keep timezone consistent for AnonymousUser by reusing location admin timezone.
        # If we call get_user_timezone(user) without location, it falls back to Asia/Kolkata and breaks
        # comparisons against now_local/start_dt/end_dt computed earlier.
        user_tz = get_user_timezone(user, location=employee_location)
        # Get start and end of day in user's timezone, then convert to UTC for filtering
        start_of_day_local = user_tz.localize(datetime.combine(today, datetime.min.time()))
        end_of_day_local = user_tz.localize(datetime.combine(today, datetime.max.time().replace(microsecond=999999)))
        start_of_day_utc = start_of_day_local.astimezone(pytz.UTC)
        end_of_day_utc = end_of_day_local.astimezone(pytz.UTC)

        try:
            tz_name2 = getattr(user_tz, "zone", str(user_tz))
        except Exception:
            tz_name2 = str(user_tz)
        logger.info(
            "ATTN STEP LOGFILTER-1: tz=%s today_local=%s start_of_day_local=%s end_of_day_local=%s start_utc=%s end_utc=%s",
            tz_name2,
            today,
            start_of_day_local,
            end_of_day_local,
            start_of_day_utc,
            end_of_day_utc,
        )
        
        logs_today = AttendanceLog.objects.filter(
            employee=matched_employee,
            timestamp__gte=start_of_day_utc,
            timestamp__lte=end_of_day_utc
        )
        logger.info(f"DEBUG: Filtering logs - today_local={today}, start_utc={start_of_day_utc}, end_utc={end_of_day_utc}, logs_today={logs_today.count()}")
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
        logger.info(f"DEBUG: last_log={last_log}")
        if not last_log:
            entry_type = "checkin"
        else:
            entry_type = "checkout" if last_log.type == "checkin" else "checkin"
        logger.info(f"DEBUG: entry_type={entry_type}")
        logger.info(f"DEBUG: has_checkin={has_checkin}, has_checkout={has_checkout}, last_log_type={last_log.type if last_log else None}")
        
        # --- 5-Minute Cooldown Check (Check BEFORE "already marked") ---
        # Check if this employee has marked attendance in the last 5 minutes
        five_minutes_ago = now - timedelta(minutes=5)
        recent_log = AttendanceLog.objects.filter(
            employee=matched_employee,
            timestamp__gte=five_minutes_ago
        ).order_by('-timestamp').first()

        if recent_log:
            time_since_last = now - recent_log.timestamp
            cooldown_duration = timedelta(minutes=5)
            seconds_remaining = int((cooldown_duration - time_since_last).total_seconds())
            
            if seconds_remaining > 0:
                action_readable = "check-out" if recent_log.type == "checkout" else "check-in"
                return Response({
                    "error": "Too soon to mark attendance",
                    "message": f"Please wait, your {action_readable} has already been recorded.",
                    "seconds_remaining": max(1, seconds_remaining),
                    "last_attendance": recent_log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_type": recent_log.type,
                }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        # --- Shift timing check ---
        # Note: "already marked" check removed - cooldown check handles the per-employee 5-min limit
        # If they've completed checkin+checkout, they can check in again after 5 mins
        status_label = "Checked-in"
        minutes_late = None
        minutes_early = None
        checkout_delta_min = None
        worked_min = None
        shift_min = None
        diff_min = None

        if shift:
            in_base, in_grace, status_hint = self.in_shift_window(now, shift, tz=user_tz)

            # Use the same timezone (user_tz) that was calculated earlier for consistency
            # This ensures shift timing validation uses the correct timezone (user's or location admin's)
            tz = user_tz  # Use user/location timezone, not Django's default
            # now_local was already calculated above using user_tz, so use it directly
            # now_local = now.astimezone(user_tz) - already calculated at line 1374
            start_time = shift.start_time
            end_time = shift.end_time

            try:
                tz_name3 = getattr(tz, "zone", str(tz))
            except Exception:
                tz_name3 = str(tz)
            logger.info(
                "ATTN STEP SHIFT-1: employee_id=%s shift_id=%s shift_name=%s tz=%s now_local=%s start_time=%s end_time=%s in_base=%s in_grace=%s",
                getattr(matched_employee, "id", None),
                getattr(shift, "id", None),
                getattr(shift, "shift_name", None),
                tz_name3,
                now_local,
                start_time,
                end_time,
                in_base,
                in_grace,
            )
            
            # Determine the correct shift window (handling overnight shifts and ±1h windows)
            # We check if 'now_local' falls into today's shift or yesterday's shift window
            def get_shift_window(anchor_date):
                s_naive = datetime.combine(anchor_date, start_time)
                if end_time > start_time:
                    e_naive = datetime.combine(anchor_date, end_time)
                else:
                    e_naive = datetime.combine(anchor_date + timedelta(days=1), end_time)
                
                s = tz.localize(s_naive) if hasattr(tz, 'localize') else timezone.make_aware(s_naive, tz)
                e = tz.localize(e_naive) if hasattr(tz, 'localize') else timezone.make_aware(e_naive, tz)
                return s, e

            # Check two potential windows: the one starting today and the one starting yesterday
            st_today, et_today = get_shift_window(now_local.date())
            st_yest, et_yest = get_shift_window(now_local.date() - timedelta(days=1))

            if (st_today - timedelta(hours=1)) <= now_local <= (et_today + timedelta(hours=1)):
                start_dt, end_dt = st_today, et_today
            elif (st_yest - timedelta(hours=1)) <= now_local <= (et_yest + timedelta(hours=1)):
                start_dt, end_dt = st_yest, et_yest
            else:
                # Default to today's window for the error message
                start_dt, end_dt = st_today, et_today

            logger.info(
                "ATTN STEP SHIFT-2: st_today=%s et_today=%s st_yest=%s et_yest=%s selected_start=%s selected_end=%s allowed_from=%s allowed_until=%s",
                st_today,
                et_today,
                st_yest,
                et_yest,
                start_dt,
                end_dt,
                (start_dt - timedelta(hours=1)),
                (end_dt + timedelta(hours=1)),
            )

            # ✅ RELAXED SHIFT TIMING: Allow marking attendance 1 hour before and 1 hour after shift
            if now_local < (start_dt - timedelta(hours=1)) or now_local > (end_dt + timedelta(hours=1)):
                logger.info(
                    "ATTN STEP SHIFT-3: OUTSIDE WINDOW now_local=%s allowed_from=%s allowed_until=%s tz=%s",
                    now_local,
                    (start_dt - timedelta(hours=1)),
                    (end_dt + timedelta(hours=1)),
                    tz_name3,
                )
                return Response(
                    {
                        "error": "Outside shift timing",
                        "message": f"Your shift timing is {start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}. You can only mark attendance from 1 hour before the start until 1 hour after the end.",
                        "shift_time": f"{start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}",
                        "current_time": now_local.strftime('%I:%M %p'),
                        "allowed_from": (start_dt - timedelta(hours=1)).strftime('%I:%M %p'),
                        "allowed_until": (end_dt + timedelta(hours=1)).strftime('%I:%M %p')
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

    def in_shift_window(self, now, shift, tz=None):
        if tz is None:
            tz = timezone.get_current_timezone()

        if timezone.is_naive(now):
            now = timezone.make_aware(now, pytz.UTC)

        # Ensure we're comparing in the correct timezone
        now_local = now.astimezone(tz)

        start_time = shift.start_time
        end_time = shift.end_time

        def get_shift_window(anchor_date):
            s_naive = datetime.combine(anchor_date, start_time)
            if end_time > start_time:
                e_naive = datetime.combine(anchor_date, end_time)
            else:
                e_naive = datetime.combine(anchor_date + timedelta(days=1), end_time)
            
            s = tz.localize(s_naive) if hasattr(tz, 'localize') else timezone.make_aware(s_naive, tz)
            e = tz.localize(e_naive) if hasattr(tz, 'localize') else timezone.make_aware(e_naive, tz)
            return s, e

        # Check today's shift and yesterday's shift
        st_today, et_today = get_shift_window(now_local.date())
        st_yest, et_yest = get_shift_window(now_local.date() - timedelta(days=1))

        if (st_today - timedelta(hours=1)) <= now_local <= (et_today + timedelta(hours=1)):
            start_dt, end_dt = st_today, et_today
        elif (st_yest - timedelta(hours=1)) <= now_local <= (et_yest + timedelta(hours=1)):
            start_dt, end_dt = st_yest, et_yest
        else:
            start_dt, end_dt = st_today, et_today

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


def calculate_attendance_summary(employees, start_date, end_date, user=None, location=None):
    """
    Shared helper function to calculate attendance summary for all employees.
    Handles multiple check-ins/check-outs by pairing sequentially and summing durations.
    Correctly handles overnight shifts where checkin and checkout span midnight.
    
    Args:
        employees: QuerySet of employees
        start_date: Start date in user's timezone
        end_date: End date in user's timezone
        user: User object to determine timezone (defaults to Asia/Kolkata)
        location: Optional Location object to use location admin timezone (for superadmin)
    
    Returns: List of dictionaries with attendance summary data
    """
    summary = []
    # If location is provided, use location admin timezone (for superadmin viewing specific location)
    if location:
        user_tz = get_timezone_from_location(location)
    else:
        user_tz = get_user_timezone(user)
    
    # Convert date range to UTC datetime range for filtering
    # Start of first day in user's timezone
    start_datetime_local = user_tz.localize(datetime.combine(start_date, datetime.min.time()))
    # End of last day in user's timezone
    end_datetime_local = user_tz.localize(datetime.combine(end_date, datetime.max.time().replace(microsecond=999999)))
    # Convert to UTC
    start_datetime_utc = start_datetime_local.astimezone(pytz.UTC)
    end_datetime_utc = end_datetime_local.astimezone(pytz.UTC)

    # Generate all dates in range (in user's timezone)
    all_dates = []
    current_date = start_date
    while current_date <= end_date:
        all_dates.append(current_date)
        current_date += timedelta(days=1)

    from django.db.models import Q
    
    for emp in employees:
        # Filter logs by UTC datetime range
        logs = emp.attendancelog_set.filter(
            timestamp__gte=start_datetime_utc,
            timestamp__lte=end_datetime_utc
        ).select_related('shift', 'site')
        
        # Group logs by date in user's timezone
        logs_by_date = defaultdict(list)
        for log in logs:
            # Convert UTC timestamp to user's timezone and get date
            if timezone.is_naive(log.timestamp):
                log_timestamp = timezone.make_aware(log.timestamp, pytz.UTC)
            else:
                log_timestamp = log.timestamp
            log_date_local = log_timestamp.astimezone(user_tz).date()
            logs_by_date[log_date_local].append(log)
        
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
            
            # For overnight shifts, we need to check logs from previous day AND next day
            # This handles consecutive overnight shifts correctly:
            # - Jan 15 22:00 checkin needs Jan 16 06:00 checkout (from next day)
            # - Jan 16 22:00 checkin needs Jan 17 06:00 checkout (from next day)
            prev_date = log_date - timedelta(days=1)
            next_date = log_date + timedelta(days=1)
            prev_date_logs = logs_by_date.get(prev_date, [])
            next_date_logs = logs_by_date.get(next_date, [])
            
            # Combine logs from previous, current, and next date for pairing
            # This ensures we can pair overnight shifts that span multiple days
            all_relevant_logs = prev_date_logs + date_logs + next_date_logs
            
            # Separate checkins and checkouts, sorted by timestamp
            checkin_logs = sorted([log for log in all_relevant_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
            checkout_logs = sorted([log for log in all_relevant_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
            
            # Use helper function to pair checkins and checkouts (handles overnight shifts)
            valid_pairs = _pair_overnight_checkins_checkouts(checkin_logs, checkout_logs, user_tz)
            
            # Filter pairs where checkin is on current date (for overnight shifts, checkin date is the shift date)
            pairs_for_this_date = []
            for checkin_log, checkout_log in valid_pairs:
                # Convert checkin timestamp to local timezone to get date
                checkin_ts = checkin_log.timestamp
                if timezone.is_naive(checkin_ts):
                    checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                checkin_date_local = checkin_ts.astimezone(user_tz).date()
                
                # Include pair if checkin is on current date (this is the shift start date)
                if checkin_date_local == log_date:
                    pairs_for_this_date.append((checkin_log, checkout_log))
            
            # Get unpaired checkins and checkouts for this date
            paired_checkin_ids = {log.id for pair in pairs_for_this_date for log in [pair[0]]}
            paired_checkout_ids = {log.id for pair in pairs_for_this_date for log in [pair[1]]}
            
            unpaired_checkins = [log for log in date_logs if log.type == 'checkin' and log.id not in paired_checkin_ids]
            unpaired_checkouts = [log for log in date_logs if log.type == 'checkout' and log.id not in paired_checkout_ids]
            
            # Get earliest checkin and latest checkout for display (from pairs or unpaired)
            all_checkins_for_date = [pair[0] for pair in pairs_for_this_date] + unpaired_checkins
            all_checkouts_for_date = [pair[1] for pair in pairs_for_this_date] + unpaired_checkouts
            
            earliest_checkin_log = min(all_checkins_for_date, key=lambda x: x.timestamp) if all_checkins_for_date else None
            latest_checkout_log = max(all_checkouts_for_date, key=lambda x: x.timestamp) if all_checkouts_for_date else None
            
            # For display purposes (return in response) - Convert UTC timestamps to user's timezone
            checkin_time = None
            checkout_time = None
            if earliest_checkin_log:
                checkin_ts_utc = earliest_checkin_log.timestamp
                if timezone.is_naive(checkin_ts_utc):
                    checkin_ts_utc = timezone.make_aware(checkin_ts_utc, pytz.UTC)
                checkin_time = checkin_ts_utc.astimezone(user_tz)
            if latest_checkout_log:
                checkout_ts_utc = latest_checkout_log.timestamp
                if timezone.is_naive(checkout_ts_utc):
                    checkout_ts_utc = timezone.make_aware(checkout_ts_utc, pytz.UTC)
                checkout_time = checkout_ts_utc.astimezone(user_tz)
            
            # Track multiple entries info (count from date_logs only, not including prev_date)
            checkin_count = len([log for log in date_logs if log.type == 'checkin'])
            checkout_count = len([log for log in date_logs if log.type == 'checkout'])
            # Also count pairs that started on this date (for overnight shifts)
            total_checkins = len([pair[0] for pair in pairs_for_this_date]) + len(unpaired_checkins)
            total_checkouts = len([pair[1] for pair in pairs_for_this_date]) + len(unpaired_checkouts)
            has_multiple_entries = (total_checkins > 1) or (total_checkouts > 1)
            
            # Calculate total duration using the paired logs (handles overnight shifts correctly)
            total_worked_seconds = 0
            paired_count = len(pairs_for_this_date)
            pair_details = []  # Store details of each valid pair
            
            # Process pairs for this date (including overnight shifts)
            for pair_num, (checkin_log, checkout_log) in enumerate(pairs_for_this_date, 1):
                    # Make timezone-aware (timestamps are stored in UTC)
                    checkin_ts = checkin_log.timestamp
                    checkout_ts = checkout_log.timestamp
                    
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                    if timezone.is_naive(checkout_ts):
                        checkout_ts = timezone.make_aware(checkout_ts, pytz.UTC)
                    
                    # Calculate duration for this pair (both in UTC, so difference is correct)
                    pair_duration = (checkout_ts - checkin_ts).total_seconds()
                    
                    # Only add positive durations (safety check)
                    if pair_duration > 0:
                        total_worked_seconds += pair_duration
                        
                        # Store pair details for frontend - Convert UTC timestamps to user's timezone
                        hours = int(pair_duration // 3600)
                        minutes = int((pair_duration % 3600) // 60)
                        # Convert timestamps to user's timezone for display
                        checkin_ts_local = checkin_ts.astimezone(user_tz)
                        checkout_ts_local = checkout_ts.astimezone(user_tz)
                        pair_details.append({
                            "pair_number": pair_num,
                            "checkin": checkin_ts_local.strftime("%Y-%m-%d %H:%M:%S"),
                            "checkout": checkout_ts_local.strftime("%Y-%m-%d %H:%M:%S"),
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
            
            # Times are already converted to user's timezone above, no need to convert again
            # (They're already timezone-aware in user_tz)
            
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
                
                # Create aware datetime objects for today in user's timezone
                shift_start_dt = user_tz.localize(
                    datetime.combine(log_date, shift_start_time)
                )
                shift_end_dt = user_tz.localize(
                    datetime.combine(log_date, shift_end_time)
                )
                
                # Handle night shifts (end_time < start_time)
                if shift_end_time < shift_start_time:
                    shift_end_dt = user_tz.localize(
                        datetime.combine(log_date + timedelta(days=1), shift_end_time)
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
        # Get today's date in user's timezone
        today = get_user_local_date(request.user, timezone.now())
        
        # Get location_id parameter for superadmin
        location_id = request.query_params.get('location_id')
        selected_location = None
        
        # For superadmin, use location_id if provided to get location admin timezone
        if request.user.role == User.Role.SUPERADMIN and location_id:
            try:
                selected_location = Location.objects.get(pk=location_id, is_deleted=False)
            except Location.DoesNotExist:
                pass
        
        # Determine timezone: use location admin timezone if location is selected, otherwise user timezone
        if selected_location:
            user_tz = get_timezone_from_location(selected_location)
            today = timezone.now().astimezone(user_tz).date()
        else:
            user_tz = get_user_timezone(request.user)
            today = get_user_local_date(request.user, timezone.now())
        
        logger.info(f"Attendance summary requested. User={request.user.email if request.user else None}, location_id={location_id}, timezone={user_tz.zone}, today={today}")
        
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
        elif request.user.role == User.Role.SUPERADMIN and location_id:
            employees = employees.filter(location_id=location_id)

        # Use shared helper function with appropriate timezone
        # For superadmin with location, pass location to get correct timezone
        summary = calculate_attendance_summary(employees, start_date, end_date, user=request.user, location=selected_location)
        
        return Response(summary)


class AttendanceSummaryExportView(AuthenticatedAPIView):
    def get(self, request):
        # Get today's date in user's timezone
        today = get_user_local_date(request.user, timezone.now())
        
        # Get location_id parameter for superadmin
        location_id = request.query_params.get('location_id')
        selected_location = None
        
        # For superadmin, use location_id if provided to get location admin timezone
        if request.user.role == User.Role.SUPERADMIN and location_id:
            try:
                selected_location = Location.objects.get(pk=location_id, is_deleted=False)
            except Location.DoesNotExist:
                pass
        
        # Determine timezone: use location admin timezone if location is selected, otherwise user timezone
        if selected_location:
            user_tz = get_timezone_from_location(selected_location)
            today = timezone.now().astimezone(user_tz).date()
        else:
            user_tz = get_user_timezone(request.user)
            today = get_user_local_date(request.user, timezone.now())
        
        logger.info(f"Attendance export requested. User={request.user.email if request.user else None}, location_id={location_id}, timezone={user_tz.zone}, today={today}")
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
        elif request.user.role == User.Role.SUPERADMIN and location_id:
            employees = employees.filter(location_id=location_id)
        
        # Use shared helper function with appropriate timezone
        summary = calculate_attendance_summary(employees, start_date, end_date, user=request.user, location=selected_location)
        
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
            punch_records = self._build_punch_records(row_data, user=request.user)
            
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
        
        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename).replace("http://", "https://")
        logger.info(f"File URL: {file_url}")
        logger.info(f"File URL: {request.build_absolute_uri(settings.MEDIA_URL + filename)}")
        return Response({"file_url": file_url})
    
    def _build_punch_records(self, row_data, user=None):
        """
        Build a user-friendly string showing all check-in/checkout sessions with durations.
        Format: "08:00 AM - 12:00 PM (4h 0m) | 01:00 PM - 06:00 PM (5h 0m)"
        """
        employee_name = row_data.get("name")
        date_str = row_data.get("date")
        
        if not employee_name or not date_str:
            return ""
        
        try:
            # Parse date (this is already in user's timezone from calculate_attendance_summary)
            check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Get employee
            employee = Employee.objects.filter(name=employee_name).first()
            if not employee:
                return ""
            
            # Convert date range to UTC for filtering
            user_tz = get_user_timezone(user)
            start_datetime_local = user_tz.localize(datetime.combine(check_date, datetime.min.time()))
            end_datetime_local = user_tz.localize(datetime.combine(check_date, datetime.max.time().replace(microsecond=999999)))
            start_datetime_utc = start_datetime_local.astimezone(pytz.UTC)
            end_datetime_utc = end_datetime_local.astimezone(pytz.UTC)
            
            # Get all logs for this employee on this date (in user's timezone)
            logs = AttendanceLog.objects.filter(
                employee=employee,
                timestamp__gte=start_datetime_utc,
                timestamp__lte=end_datetime_utc
            ).order_by('timestamp')
            
            if not logs.exists():
                return ""
            
            # Build sessions (convert timestamps to user's timezone for display)
            sessions = []
            checkin_time = None
            
            for log in logs:
                # Convert UTC timestamp to user's timezone
                if timezone.is_naive(log.timestamp):
                    log_timestamp = timezone.make_aware(log.timestamp, pytz.UTC)
                else:
                    log_timestamp = log.timestamp
                log_timestamp_local = log_timestamp.astimezone(user_tz)
                
                if log.type == 'checkin':
                    checkin_time = log_timestamp_local
                elif log.type == 'checkout' and checkin_time:
                    # Calculate duration
                    duration = log_timestamp_local - checkin_time
                    hours = int(duration.total_seconds() // 3600)
                    minutes = int((duration.total_seconds() % 3600) // 60)
                    
                    # Format session
                    session_str = f"{checkin_time.strftime('%I:%M %p')} - {log_timestamp_local.strftime('%I:%M %p')} ({hours}h {minutes}m)"
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
        logger.info(f"File URL: {file_url}")
        logger.info(f"File URL: {request.build_absolute_uri(settings.MEDIA_URL + filename)}")
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

        # Get location_id parameter for superadmin
        location_id = request.query_params.get('location_id')
        selected_location = None
        
        # For superadmin, use location_id if provided to get location admin timezone
        if request.user.role == User.Role.SUPERADMIN and location_id:
            try:
                selected_location = Location.objects.get(pk=location_id, is_deleted=False)
            except Location.DoesNotExist:
                pass

        # Convert date range to UTC for filtering
        # Use location admin timezone if location is selected for superadmin
        if selected_location:
            user_tz = get_timezone_from_location(selected_location)
        else:
            user_tz = get_user_timezone(request.user)
        start_datetime_local = user_tz.localize(datetime.combine(start_date, datetime.min.time()))
        end_datetime_local = user_tz.localize(datetime.combine(end_date, datetime.max.time().replace(microsecond=999999)))
        start_datetime_utc = start_datetime_local.astimezone(pytz.UTC)
        end_datetime_utc = end_datetime_local.astimezone(pytz.UTC)

        date_range = [
            start_date + timedelta(days=i)
            for i in range((end_date - start_date).days + 1)
        ]
        employees = Employee.objects.select_related("location").all()
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)
        elif request.user.role == User.Role.SUPERADMIN and location_id:
            employees = employees.filter(location_id=location_id)

        # Extend date range to include previous and next day for overnight shift handling
        # This ensures overnight shifts at month boundaries are captured correctly
        extended_start_date = start_date - timedelta(days=1)
        extended_end_date = end_date + timedelta(days=1)
        extended_start_datetime_local = user_tz.localize(datetime.combine(extended_start_date, datetime.min.time()))
        extended_end_datetime_local = user_tz.localize(datetime.combine(extended_end_date, datetime.max.time().replace(microsecond=999999)))
        extended_start_datetime_utc = extended_start_datetime_local.astimezone(pytz.UTC)
        extended_end_datetime_utc = extended_end_datetime_local.astimezone(pytz.UTC)
        
        logs = AttendanceLog.objects.filter(
            timestamp__gte=extended_start_datetime_utc,
            timestamp__lte=extended_end_datetime_utc
        )
        if request.user.role == User.Role.ADMIN:
            logs = logs.filter(employee__location=request.user.location)
        elif request.user.role == User.Role.SUPERADMIN and location_id:
            logs = logs.filter(employee__location_id=location_id)

        # Group logs by employee first, then pair checkins/checkouts (handles overnight shifts)
        logs_by_employee = defaultdict(list)
        for log in logs:
            try:
                # Convert UTC timestamp to user's timezone
                if timezone.is_naive(log.timestamp):
                    log_timestamp = timezone.make_aware(log.timestamp, pytz.UTC)
                else:
                    log_timestamp = log.timestamp
            except Exception:
                continue
            logs_by_employee[log.employee_id].append(log)

        attendance_map = defaultdict(dict)
        
        # Process each employee's logs
        for emp_id, emp_logs in logs_by_employee.items():
            # Separate checkins and checkouts
            checkin_logs = sorted([log for log in emp_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
            checkout_logs = sorted([log for log in emp_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
            
            # Use helper function to pair checkins and checkouts (handles overnight shifts)
            valid_pairs = _pair_overnight_checkins_checkouts(checkin_logs, checkout_logs, user_tz)
            
            # Group pairs by checkin date (shift start date for overnight shifts)
            pairs_by_date = defaultdict(list)
            for checkin_log, checkout_log in valid_pairs:
                # Get checkin date in user's timezone (this is the shift date)
                checkin_ts = checkin_log.timestamp
                if timezone.is_naive(checkin_ts):
                    checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                checkin_date = checkin_ts.astimezone(user_tz).date()
                pairs_by_date[checkin_date].append((checkin_log, checkout_log))
            
            # Process pairs for each date (only include dates within the month range)
            for checkin_date, pairs in pairs_by_date.items():
                # Only process pairs where checkin date is within the requested month range
                if checkin_date < start_date or checkin_date > end_date:
                    continue
                
                # Calculate total worked seconds for all pairs on this date
                total_worked_seconds = 0
                for checkin_log, checkout_log in pairs:
                    checkin_ts = checkin_log.timestamp
                    checkout_ts = checkout_log.timestamp
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                    if timezone.is_naive(checkout_ts):
                        checkout_ts = timezone.make_aware(checkout_ts, pytz.UTC)
                    pair_duration = (checkout_ts - checkin_ts).total_seconds()
                    if pair_duration > 0:
                        total_worked_seconds += pair_duration
                
                # Use same duration-based logic as Today's report (3h/6h thresholds)
                if total_worked_seconds < 3 * 3600:
                    # Worked less than 3 hours = Absent
                    attendance_map[emp_id][checkin_date] = 'A'
                elif total_worked_seconds < 6 * 3600:
                    # Worked 3-6 hours = Half day Present
                    attendance_map[emp_id][checkin_date] = 'HP'
                else:
                    # Worked 6+ hours = Present
                    attendance_map[emp_id][checkin_date] = 'P'
            
            # Handle unpaired checkins/checkouts (mark as Absent)
            # Only process logs within the month range
            paired_checkin_ids = {log.id for pairs in pairs_by_date.values() for log, _ in pairs}
            paired_checkout_ids = {log.id for pairs in pairs_by_date.values() for _, log in pairs}
            
            for log in emp_logs:
                if log.id in paired_checkin_ids or log.id in paired_checkout_ids:
                    continue
                
                log_ts = log.timestamp
                if timezone.is_naive(log_ts):
                    log_ts = timezone.make_aware(log_ts, pytz.UTC)
                log_date = log_ts.astimezone(user_tz).date()
                
                # Only process logs within the month range
                if log_date < start_date or log_date > end_date:
                    continue
                
                # Unpaired checkin or checkout = Absent
                if log_date not in attendance_map[emp_id]:
                    attendance_map[emp_id][log_date] = 'A'

        summary = []
        for emp in employees:
            row = {"name": emp.name}
            for day in date_range:
                if emp.id in attendance_map and day in attendance_map[emp.id]:
                    status_code = attendance_map[emp.id][day]
                else:
                    # Check if employee has any attendance records in this month
                    # If yes, missing days are Absent. If no, show "-" (no data)
                    has_any_records = (emp.id in attendance_map and len(attendance_map[emp.id]) > 0) or emp.id in logs_by_employee
                    status_code = "A" if has_any_records else "-"
                row[day.strftime("%d-%b")] = status_code
            summary.append(row)

        return Response(summary)


class MonthlyAttendanceStatusExportView(AuthenticatedAPIView):
    def get(self, request):
        month = request.query_params.get("month")
        if not month:
            return Response({"error": "Month is required in YYYY-MM format"}, status=400)

        # Get location_id parameter for superadmin
        location_id = request.query_params.get('location_id')
        selected_location = None
        
        # For superadmin, use location_id if provided to get location admin timezone
        if request.user.role == User.Role.SUPERADMIN and location_id:
            try:
                selected_location = Location.objects.get(pk=location_id, is_deleted=False)
            except Location.DoesNotExist:
                pass

        try:
            year, month_num = map(int, month.split("-"))
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except Exception:
            return Response({"error": "Invalid month format"}, status=400)

        # Convert date range to UTC for filtering
        # Use location admin timezone if location is selected for superadmin
        if selected_location:
            user_tz = get_timezone_from_location(selected_location)
        else:
            user_tz = get_user_timezone(request.user)
        start_datetime_local = user_tz.localize(datetime.combine(start_date, datetime.min.time()))
        end_datetime_local = user_tz.localize(datetime.combine(end_date, datetime.max.time().replace(microsecond=999999)))
        start_datetime_utc = start_datetime_local.astimezone(pytz.UTC)
        end_datetime_utc = end_datetime_local.astimezone(pytz.UTC)

        date_range = [
            start_date + timedelta(days=i)
            for i in range((end_date - start_date).days + 1)
        ]
        employees = Employee.objects.select_related("location").all()
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)
        elif request.user.role == User.Role.SUPERADMIN and location_id:
            employees = employees.filter(location_id=location_id)

        # Extend date range to include previous and next day for overnight shift handling
        # This ensures overnight shifts at month boundaries are captured correctly
        extended_start_date = start_date - timedelta(days=1)
        extended_end_date = end_date + timedelta(days=1)
        extended_start_datetime_local = user_tz.localize(datetime.combine(extended_start_date, datetime.min.time()))
        extended_end_datetime_local = user_tz.localize(datetime.combine(extended_end_date, datetime.max.time().replace(microsecond=999999)))
        extended_start_datetime_utc = extended_start_datetime_local.astimezone(pytz.UTC)
        extended_end_datetime_utc = extended_end_datetime_local.astimezone(pytz.UTC)
        
        logs = AttendanceLog.objects.filter(
            timestamp__gte=extended_start_datetime_utc,
            timestamp__lte=extended_end_datetime_utc
        )
        if request.user.role == User.Role.ADMIN:
            logs = logs.filter(employee__location=request.user.location)
        elif request.user.role == User.Role.SUPERADMIN and location_id:
            logs = logs.filter(employee__location_id=location_id)

        # Use same logic as MonthlyAttendanceStatusView - group by employee, then pair checkins/checkouts
        logs_by_employee = defaultdict(list)
        for log in logs:
            try:
                # Convert UTC timestamp to user's timezone
                if timezone.is_naive(log.timestamp):
                    log_timestamp = timezone.make_aware(log.timestamp, pytz.UTC)
                else:
                    log_timestamp = log.timestamp
            except Exception:
                continue
            logs_by_employee[log.employee_id].append(log)

        attendance_map = {}
        
        # Process each employee's logs
        for emp_id, emp_logs in logs_by_employee.items():
            # Separate checkins and checkouts
            checkin_logs = sorted([log for log in emp_logs if log.type == 'checkin'], key=lambda x: x.timestamp)
            checkout_logs = sorted([log for log in emp_logs if log.type == 'checkout'], key=lambda x: x.timestamp)
            
            # Use helper function to pair checkins and checkouts (handles overnight shifts)
            valid_pairs = _pair_overnight_checkins_checkouts(checkin_logs, checkout_logs, user_tz)
            
            # Group pairs by checkin date (shift start date for overnight shifts)
            pairs_by_date = defaultdict(list)
            for checkin_log, checkout_log in valid_pairs:
                # Get checkin date in user's timezone (this is the shift date)
                checkin_ts = checkin_log.timestamp
                if timezone.is_naive(checkin_ts):
                    checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                checkin_date = checkin_ts.astimezone(user_tz).date()
                pairs_by_date[checkin_date].append((checkin_log, checkout_log))
            
            # Process pairs for each date (only include dates within the month range)
            for checkin_date, pairs in pairs_by_date.items():
                # Only process pairs where checkin date is within the requested month range
                if checkin_date < start_date or checkin_date > end_date:
                    continue
                
                # Calculate total worked seconds for all pairs on this date
                total_worked_seconds = 0
                for checkin_log, checkout_log in pairs:
                    checkin_ts = checkin_log.timestamp
                    checkout_ts = checkout_log.timestamp
                    if timezone.is_naive(checkin_ts):
                        checkin_ts = timezone.make_aware(checkin_ts, pytz.UTC)
                    if timezone.is_naive(checkout_ts):
                        checkout_ts = timezone.make_aware(checkout_ts, pytz.UTC)
                    pair_duration = (checkout_ts - checkin_ts).total_seconds()
                    if pair_duration > 0:
                        total_worked_seconds += pair_duration
                
                # Use same duration-based logic as Today's report (3h/6h thresholds)
                if total_worked_seconds < 3 * 3600:
                    # Worked less than 3 hours = Absent
                    attendance_map[(emp_id, checkin_date)] = 'A'
                elif total_worked_seconds < 6 * 3600:
                    # Worked 3-6 hours = Half day Present
                    attendance_map[(emp_id, checkin_date)] = 'HP'
                else:
                    # Worked 6+ hours = Present
                    attendance_map[(emp_id, checkin_date)] = 'P'
            
            # Handle unpaired checkins/checkouts (mark as Absent)
            paired_checkin_ids = {log.id for pairs in pairs_by_date.values() for log, _ in pairs}
            paired_checkout_ids = {log.id for pairs in pairs_by_date.values() for _, log in pairs}
            
            for log in emp_logs:
                if log.id in paired_checkin_ids or log.id in paired_checkout_ids:
                    continue
                
                log_ts = log.timestamp
                if timezone.is_naive(log_ts):
                    log_ts = timezone.make_aware(log_ts, pytz.UTC)
                log_date = log_ts.astimezone(user_tz).date()
                
                # Only process logs within the month range
                if log_date < start_date or log_date > end_date:
                    continue
                
                # Unpaired checkin or checkout = Absent
                if (emp_id, log_date) not in attendance_map:
                    attendance_map[(emp_id, log_date)] = 'A'

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
                elif status_code == "HP":
                    # Half day Present counts as 0.5 present and 0.5 absent
                    present_count += 0.5
                    absent_count += 0.5
                elif status_code == "A":
                    absent_count += 1

            row.append(present_count)
            row.append(absent_count)
            ws.append(row)

        filename = f"monthly_attendance_{month}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename).replace("http://", "https://")
        logger.info(f"Monthly export file URL: {file_url}")
        return Response({"file_url": file_url})


class GeneratePayrollView(AuthenticatedAPIView):
    def post(self, request):
        month = request.data.get("month")
        if not month:
            return Response({"error": "Month is required"}, status=400)

        year, month_num = map(int, month.split("-"))
        start_date = datetime(year, month_num, 1).date()
        end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()

        # Convert date range to UTC for filtering
        user_tz = get_user_timezone(request.user)
        start_datetime_local = user_tz.localize(datetime.combine(start_date, datetime.min.time()))
        end_datetime_local = user_tz.localize(datetime.combine(end_date, datetime.max.time().replace(microsecond=999999)))
        start_datetime_utc = start_datetime_local.astimezone(pytz.UTC)
        end_datetime_utc = end_datetime_local.astimezone(pytz.UTC)

        employees = Employee.objects.all()
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)

        logs = AttendanceLog.objects.filter(
            timestamp__gte=start_datetime_utc,
            timestamp__lte=end_datetime_utc
        )
        if request.user.role == User.Role.ADMIN:
            logs = logs.filter(employee__location=request.user.location)

        attendance_map = {}
        for log in logs:
            # Convert UTC timestamp to user's timezone and get date
            if timezone.is_naive(log.timestamp):
                log_timestamp = timezone.make_aware(log.timestamp, pytz.UTC)
            else:
                log_timestamp = log.timestamp
            log_date = log_timestamp.astimezone(user_tz).date()
            key = (log.employee_id, log_date)
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