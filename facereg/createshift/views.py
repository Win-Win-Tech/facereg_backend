from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from regface.models import User, Location, Employee, Shift, Site, AttendanceLog, PayrollRecord, Assignment, UserSite
from .serializers import (
    UserSerializer, LocationSerializer, EmployeeSerializer, ShiftSerializer,
    SiteSerializer, AttendanceLogSerializer, PayrollRecordSerializer,
    AssignmentSerializer, UserSiteSerializer
)
from django.shortcuts import get_object_or_404

class LocationViewSet(viewsets.ModelViewSet):
    queryset = Location.objects.all()
    serializer_class = LocationSerializer


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer


class EmployeeViewSet(viewsets.ModelViewSet):
    queryset = Employee.objects.all()
    serializer_class = EmployeeSerializer


class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer


class SiteViewSet(viewsets.ModelViewSet):
    queryset = Site.objects.all()
    serializer_class = SiteSerializer


class AttendanceLogViewSet(viewsets.ModelViewSet):
    queryset = AttendanceLog.objects.all()
    serializer_class = AttendanceLogSerializer


class PayrollRecordViewSet(viewsets.ModelViewSet):
    queryset = PayrollRecord.objects.all()
    serializer_class = PayrollRecordSerializer


class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer

    # Custom action: assign shift to multiple users
    @action(detail=False, methods=["post"])
    def bulk_assign(self, request):
        user_ids = request.data.get("user_ids", [])
        shift_id = request.data.get("shift_id")
        location_id = request.data.get("location_id")
#       created_by = request.data.get("created_by")
        created_by_id = request.data.get("created_by")
        created_by = get_object_or_404(User, pk=created_by_id)

        for uid in user_ids:
            Assignment.objects.update_or_create(
                user_id=uid,
                location_id=location_id,
                #defaults={"shift_id": shift_id, "created_by": request.user}
                defaults={"shift_id": shift_id, "created_by": created_by}
            )
        return Response({"status": "Shift assigned to multiple users"})


class UserSiteViewSet(viewsets.ModelViewSet):
    queryset = UserSite.objects.all()
    serializer_class = UserSiteSerializer

    # Custom action: assign sites to multiple users
    @action(detail=False, methods=["post"])
    def bulk_assign(self, request):
        user_ids = request.data.get("user_ids", [])
        site_ids = request.data.get("site_ids", [])
        created_by_id = request.data.get("created_by")
        created_by = get_object_or_404(User, pk=created_by_id)
        for uid in user_ids:
            for sid in site_ids:
                UserSite.objects.update_or_create(
                    user_id=uid,
                    site_id=sid,
                    defaults={"created_by": created_by}
                )
        return Response({"status": "Sites assigned to multiple users"})
