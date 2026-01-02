from rest_framework import serializers
from regface.models import User, Location, Employee, Shift, Site, AttendanceLog, PayrollRecord, Assignment, UserSite


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = "__all__"


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = "__all__"


class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = "__all__"


class ShiftSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    location_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    
    class Meta:
        model = Shift
        fields = "__all__"
    
    def to_internal_value(self, data):
        """Convert location_id from request body to location for model"""
        if 'location_id' in data and 'location' not in data:
            data = data.copy()
            data['location'] = data.pop('location_id')
        return super().to_internal_value(data)
    
    def to_representation(self, instance):
        """Ensure location and location_id are always included in response"""
        representation = super().to_representation(instance)
        # Get location_id from the instance
        try:
            location_id = instance.location_id
        except AttributeError:
            # If location_id doesn't exist (migration not run), try to get from location
            try:
                location_id = instance.location.id if instance.location else None
            except:
                location_id = None
        
        # Always include location and location_id in response (as UUID string or None)
        representation['location'] = str(location_id) if location_id else None
        representation['location_id'] = str(location_id) if location_id else None
        
        # Ensure location_name is included (from the default serializer behavior)
        if 'location_name' not in representation:
            representation['location_name'] = instance.location.name if instance.location else None
        
        return representation


class SiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Site
        fields = "__all__"


class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = "__all__"


class PayrollRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollRecord
        fields = "__all__"


class AssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = "__all__"


class UserSiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSite
        fields = "__all__"
