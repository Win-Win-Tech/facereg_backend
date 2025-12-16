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

logger = logging.getLogger(__name__)


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
    """List all non-deleted shifts and create new shifts."""
    def get(self, request):
        shifts = Shift.objects.filter(is_deleted=False).order_by('shift_name')
        serializer = ShiftSerializer(shifts, many=True)
        return Response(serializer.data)
    
    def post(self, request):
        serializer = ShiftSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
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
        
        shift.is_deleted = True
        shift.deleted_by = request.user
        shift.save(update_fields=['is_deleted', 'deleted_by', 'modified_on', 'modified_by'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SiteListCreateView(AuthenticatedAPIView):
    """List all non-deleted sites and create new sites."""
    def get(self, request):
        sites = Site.objects.filter(is_deleted=False).order_by('site_name')
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
        """
        Expected request data format:
        {
            "employee_ids": [1, 2, 3],
            "shift_id": "uuid",
            "location_id": "uuid",
            "site_ids": ["uuid1", "uuid2"]  # optional
        }
        """
        employee_ids = request.data.get('employee_ids', [])
        shift_id = request.data.get('shift_id')
        location_id = request.data.get('location_id')
        site_ids = request.data.get('site_ids', [])
        
        # Validation
        if not employee_ids or not isinstance(employee_ids, list):
            return Response(
                {"error": "employee_ids must be a non-empty list"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not shift_id:
            return Response(
                {"error": "shift_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify shift exists
        try:
            shift = Shift.objects.get(pk=shift_id, is_deleted=False)
        except Shift.DoesNotExist:
            return Response(
                {"error": "Shift not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
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
            sites = Site.objects.filter(pk__in=site_ids, is_deleted=False)
            if len(sites) != len(site_ids):
                return Response(
                    {"error": "One or more sites not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
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
                    # If existing assignment uses a different shift, update it to the new shift
                    if str(existing.shift_id) != str(shift_id):
                        existing.shift = shift
                        existing.modified_by = request.user
                        try:
                            existing.modified_on = timezone.now()
                        except Exception:
                            pass
                        existing.save(update_fields=["shift", "modified_on", "modified_by"])
                    # Treat existing (created or updated) as an affected assignment
                    created_assignments.append(existing)
                else:
                    assignment = Assignment.objects.create(
                        user_id=employee_id,
                        shift_id=shift_id,
                        location_id=location_id,
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

        employees = Employee.objects.filter(face_encoding__isnull=False).only(
            "id", "name", "face_encoding", "photo", "location"
        )
        user = getattr(request, "user", None)
        if isinstance(user, User) and user.role == User.Role.ADMIN:
            employees = employees.filter(location=user.location)

        known_encodings, employee_map = [], []
        for emp in employees:
            if emp.face_encoding:
                known_encodings.append(np.frombuffer(emp.face_encoding))
                employee_map.append(emp)

        if not known_encodings:
            return Response({"error": "No registered employees"}, status=status.HTTP_404_NOT_FOUND)

        distances = face_recognition.face_distance(known_encodings, uploaded_encoding)
        best_match_index = np.argmin(distances)
        if distances[best_match_index] > 0.45:
            return Response({"error": "Face not recognized"}, status=status.HTTP_404_NOT_FOUND)

        matched_employee = employee_map[best_match_index]
        today = date.today()
        now = timezone.now()

        # --- Attendance rules ---
        assignment = Assignment.objects.filter(
            user_id=matched_employee.id,
            location_id=matched_employee.location_id
        ).select_related("shift").order_by("-created_on").first()

        user_sites = UserSite.objects.filter(user_id=matched_employee.id).select_related("site")
        location_sites = Site.objects.filter(location_id=matched_employee.location_id)

        shift = assignment.shift if assignment else None
        
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
                        "error": "Outside allowed site radius",
                        "distance_m": round(nearest_distance, 2) if nearest_distance is not None else None,
                        "allowed_radius_m": round(allowed_radius, 2),
                        "site_id": str(nearest_site.id) if nearest_site else None,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
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
                status_label = "Check-out"

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
                    message = f"{emp_name}, you have worked only {hours_worked} hours {mins_worked} minutes, which is less than 4 hours. This will be marked as Half day Absent."
                else:
                    message = f"{emp_name}, you have worked less than 4 hours. This will be marked as Half day Absent."
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

        confidence = round(1 - distances[best_match_index], 2)
        photo_base64 = base64.b64encode(matched_employee.photo).decode("utf-8") if matched_employee.photo else None

        return Response({
            "status": status_label,
            "message": message,
            "employee": matched_employee.name.strip(),
            "confidence": confidence,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "photo": f"data:image/jpeg;base64,{photo_base64}" if photo_base64 else None,
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


class AttendanceSummaryView(AuthenticatedAPIView):
    def get(self, request):
        today = date.today()
        start_date = request.query_params.get('start_date', today.strftime('%Y-%m-%d'))
        end_date = request.query_params.get('end_date', today.strftime('%Y-%m-%d'))
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            start_date = today
            end_date = today
        
        summary = []

        employees = Employee.objects.select_related("location").prefetch_related("attendancelog_set")
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)

        # Generate all dates in range
        all_dates = []
        current_date = start_date
        while current_date <= end_date:
            all_dates.append(current_date)
            current_date += timedelta(days=1)

        for emp in employees:
            logs = emp.attendancelog_set.filter(timestamp__date__range=(start_date, end_date)).select_related('shift', 'site')
            
            # Group logs by date
            logs_by_date = defaultdict(list)
            for log in logs:
                log_date = log.timestamp.date()
                logs_by_date[log_date].append(log)
            
            # Get employee's shift assignment
            assignment = Assignment.objects.filter(
                user_id=emp.id,
                location_id=emp.location_id,
                is_deleted=False
            ).select_related('shift', 'location').first()
            
            shift = assignment.shift if assignment else None
            
            # Process each date in range (including dates with no logs)
            for log_date in all_dates:
                date_logs = logs_by_date.get(log_date, [])
                checkin_log = next((log for log in date_logs if log.type == 'checkin'), None)
                checkout_log = next((log for log in date_logs if log.type == 'checkout'), None)
                
                checkin_time = checkin_log.timestamp if checkin_log else None
                checkout_time = checkout_log.timestamp if checkout_log else None
                
                # Make times timezone-aware for consistent calculations
                tz = timezone.get_current_timezone()
                if checkin_time and timezone.is_naive(checkin_time):
                    checkin_time = timezone.make_aware(checkin_time, tz)
                if checkout_time and timezone.is_naive(checkout_time):
                    checkout_time = timezone.make_aware(checkout_time, tz)
                
                # Compute duration (worked time)
                duration_str = None
                worked_seconds = None
                if checkin_time and checkout_time:
                    worked_seconds = (checkout_time - checkin_time).total_seconds()
                    hours = int(worked_seconds // 3600)
                    minutes = int((worked_seconds % 3600) // 60)
                    duration_str = f"{hours:02d}:{minutes:02d}"
                
                # If no checkin on this day, mark as Absent
                if not checkin_time:
                    summary.append(
                        {
                            "date": log_date.strftime("%Y-%m-%d"),
                            "name": emp.name,
                            "department": emp.department or "—",
                            "location": emp.location.name if emp.location else "—",
                            "shift": shift.shift_name if shift else "—",
                            "shift_start": shift.start_time.strftime("%H:%M") if shift else "—",
                            "shift_end": shift.end_time.strftime("%H:%M") if shift else "—",
                            "checkin": "—",
                            "checkout": "—",
                            "duration": "—",
                            "status": "Absent",
                            "variance": "—",
                            "remarks": "Absent",
                            "note": "No check-in",
                        }
                    )
                    continue
                
                # Compute variance and remarks
                variance_str = "—"
                remarks = "—"
                note = "—"
                status_str = "Present" if checkin_time else "Absent"
                
                if shift and checkin_time:
                    # Shift times
                    shift_start_time = shift.start_time
                    shift_end_time = shift.end_time
                    
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
                        variance_seconds = worked_seconds - shift_duration_seconds
                        variance_hours = int(abs(variance_seconds) // 3600)
                        variance_mins = int((abs(variance_seconds) % 3600) // 60)
                        sign = '-' if variance_seconds < 0 else ''
                        variance_str = f"{sign}{variance_hours:02d}:{variance_mins:02d}"
                        
                        # Update note with variance details
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
                
                summary.append(
                    {
                        "date": log_date.strftime("%Y-%m-%d"),
                        "name": emp.name,
                        "department": emp.department or "—",
                        "location": emp.location.name if emp.location else "—",
                        "shift": shift.shift_name if shift else "—",
                        "shift_start": shift.start_time.strftime("%H:%M") if shift else "—",
                        "shift_end": shift.end_time.strftime("%H:%M") if shift else "—",
                        "checkin": checkin_time.strftime("%Y-%m-%d %H:%M:%S") if checkin_time else "—",
                        "checkout": checkout_time.strftime("%Y-%m-%d %H:%M:%S") if checkout_time else "—",
                        "duration": duration_str or "—",
                        "status": status_str,
                        "variance": variance_str,
                        "remarks": remarks,
                        "note": note,
                    }
                )

        return Response(summary)


class AttendanceSummaryExportView(AuthenticatedAPIView):
    def get(self, request):
        today = date.today()
        start_date = request.query_params.get('start_date', today.strftime('%Y-%m-%d'))
        end_date = request.query_params.get('end_date', today.strftime('%Y-%m-%d'))
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            start_date = today
            end_date = today
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Attendance Summary"

        ws.append(["Date", "Location", "Name", "Department", "Shift", "Shift Start", "Shift End", "Check-in", "Check-out", "Duration", "Status", "Variance", "Remarks", "Note"])

        employees = Employee.objects.select_related("location").prefetch_related("attendancelog_set")
        if request.user.role == User.Role.ADMIN:
            employees = employees.filter(location=request.user.location)

        # Generate all dates in range
        all_dates = []
        current_date = start_date
        while current_date <= end_date:
            all_dates.append(current_date)
            current_date += timedelta(days=1)

        for emp in employees:
            logs = emp.attendancelog_set.filter(timestamp__date__range=(start_date, end_date)).select_related('shift', 'site')
            
            # Group logs by date
            logs_by_date = defaultdict(list)
            for log in logs:
                log_date = log.timestamp.date()
                logs_by_date[log_date].append(log)
            
            # Get employee's shift assignment
            assignment = Assignment.objects.filter(
                user_id=emp.id,
                location_id=emp.location_id,
                is_deleted=False
            ).select_related('shift', 'location').first()
            
            shift = assignment.shift if assignment else None
            
            # Process each date in range (including dates with no logs)
            for log_date in all_dates:
                date_logs = logs_by_date.get(log_date, [])
                checkin_log = next((log for log in date_logs if log.type == 'checkin'), None)
                checkout_log = next((log for log in date_logs if log.type == 'checkout'), None)
                
                checkin_time = checkin_log.timestamp if checkin_log else None
                checkout_time = checkout_log.timestamp if checkout_log else None
                
                # Make times timezone-aware for consistent calculations
                tz = timezone.get_current_timezone()
                if checkin_time and timezone.is_naive(checkin_time):
                    checkin_time = timezone.make_aware(checkin_time, tz)
                if checkout_time and timezone.is_naive(checkout_time):
                    checkout_time = timezone.make_aware(checkout_time, tz)
                
                # Compute duration (worked time)
                duration_str = ""
                worked_seconds = None
                if checkin_time and checkout_time:
                    worked_seconds = (checkout_time - checkin_time).total_seconds()
                    hours = int(worked_seconds // 3600)
                    minutes = int((worked_seconds % 3600) // 60)
                    duration_str = f"{hours:02d}:{minutes:02d}"
                
                # If no checkin on this day, mark as Absent
                if not checkin_time:
                    ws.append([
                        log_date.strftime("%Y-%m-%d"),
                        emp.location.name if emp.location else "—",
                        emp.name,
                        emp.department or "—",
                        shift.shift_name if shift else "—",
                        shift.start_time.strftime("%H:%M") if shift else "—",
                        shift.end_time.strftime("%H:%M") if shift else "—",
                        "—",
                        "—",
                        "—",
                        "Absent",
                        "—",
                        "Absent",
                        "No check-in",
                    ])
                    continue
                
                # Compute variance and remarks
                variance_str = ""
                remarks = ""
                note = ""
                status_str = "Present" if checkin_time else "Absent"
                
                if shift and checkin_time:
                    # Shift times
                    shift_start_time = shift.start_time
                    shift_end_time = shift.end_time
                    
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
                        remarks = "Early Check-in"
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
                            remarks = "Late Check-out"
                            note = f"{abs(checkout_variance_minutes)}min late"
                        elif checkout_variance_minutes > 15:
                            remarks = "Early Check-out"
                            note = f"{checkout_variance_minutes}min early"
                        
                        # Compute variance as worked - shift duration
                        variance_seconds = worked_seconds - shift_duration_seconds
                        variance_hours = int(abs(variance_seconds) // 3600)
                        variance_mins = int((abs(variance_seconds) % 3600) // 60)
                        sign = '-' if variance_seconds < 0 else ''
                        variance_str = f"{sign}{variance_hours:02d}:{variance_mins:02d}"
                        
                        # Update note with variance details
                        if abs(variance_seconds) <= 15 * 60:
                            note = "0 to +/- 15min"
                        elif abs(variance_seconds) <= 60 * 60:
                            note = f">15min & <60min"
                        elif variance_seconds > 0:
                            note = f">+1hr (Overtime)"
                        else:
                            note = f"<-1hr (Undertime)"
                
                ws.append(
                    [
                        log_date.strftime("%Y-%m-%d"),
                        emp.location.name if emp.location else "—",
                        emp.name,
                        emp.department or "—",
                        shift.shift_name if shift else "—",
                        shift.start_time.strftime("%H:%M") if shift else "—",
                        shift.end_time.strftime("%H:%M") if shift else "—",
                        checkin_time.strftime("%H:%M:%S") if checkin_time else "",
                        checkout_time.strftime("%H:%M:%S") if checkout_time else "",
                        duration_str,
                        status_str,
                        variance_str,
                        remarks,
                        note,
                    ]
                )

        filename = f"attendance_summary_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
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

        attendance_map = defaultdict(lambda: defaultdict(lambda: "-"))
        for log in logs:
            attendance_map[log.employee_id][log.timestamp.date()] = "P"

        summary = []
        for emp in employees:
            row = {"name": emp.name}
            for day in date_range:
                status_code = attendance_map[emp.id].get(
                    day, "A" if emp.id in attendance_map else "-"
                )
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