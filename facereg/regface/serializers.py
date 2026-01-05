import base64

from django.contrib.auth.hashers import make_password
from rest_framework import serializers

from .models import Employee, Location, User, Shift, Site, Assignment, UserSite


class FaceUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()


class EmployeeRegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    location_id = serializers.PrimaryKeyRelatedField(
        source="location", queryset=Location.objects.filter(is_deleted=False)
    )
    face_image = serializers.ImageField()
    profile_photo = serializers.ImageField(required=False, allow_null=True)
    
    # Payslip fields
    gross_salary = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    payslip_field_config_id = serializers.UUIDField(required=False, allow_null=True)
    
    # Employee details (optional)
    employee_code = serializers.CharField(max_length=50, required=False, allow_null=True, allow_blank=True)
    department = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    designation = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    experience_years = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    joining_date = serializers.DateField(required=False, allow_null=True)
    
    # Banking (optional)
    bank_account_number = serializers.CharField(max_length=20, required=False, allow_null=True, allow_blank=True)
    ifsc_code = serializers.CharField(max_length=11, required=False, allow_null=True, allow_blank=True)
    bank_name = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    
    # Identification (optional)
    pan_number = serializers.CharField(max_length=10, required=False, allow_null=True, allow_blank=True)
    aadhaar_number = serializers.CharField(max_length=12, required=False, allow_null=True, allow_blank=True)
    uan_number = serializers.CharField(max_length=12, required=False, allow_null=True, allow_blank=True)
    esi_number = serializers.CharField(max_length=17, required=False, allow_null=True, allow_blank=True)
    
    # Contact (optional)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    phone = serializers.CharField(max_length=15, required=False, allow_null=True, allow_blank=True)
    address = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate(self, attrs):
        name = attrs.get("name", "").strip()
        if not name:
            raise serializers.ValidationError({"name": "Name is required."})
        return attrs


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "name", "created_at", "updated_at", "is_deleted"]
        read_only_fields = ["id", "created_at", "updated_at", "is_deleted"]


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    location_id = serializers.PrimaryKeyRelatedField(
        source="location",
        queryset=Location.objects.filter(is_deleted=False),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "role",
            "location_id",
            "timezone",
            "is_active",
            "is_deleted",
            "created_at",
            "updated_at",
            "password",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "is_deleted"]

    def validate(self, attrs):
        role = attrs.get("role", getattr(self.instance, "role", None))
        location = attrs.get("location", getattr(self.instance, "location", None))

        if role == User.Role.ADMIN and location is None:
            raise serializers.ValidationError(
                {"location_id": "Admin users must be assigned to a location."}
            )
        if role == User.Role.SUPERADMIN and location is not None:
            raise serializers.ValidationError(
                {"location_id": "Super admin users cannot be assigned to a location."}
            )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Password is required."})
        user = User(**validated_data)
        user.password = make_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.password = make_password(password)
        instance.save()
        return instance


class EmployeeListSerializer(serializers.ModelSerializer):
    location_id = serializers.UUIDField(read_only=True, allow_null=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    has_face_encoding = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = ["id", "name", "location_id", "location_name", "has_face_encoding"]

    def get_has_face_encoding(self, obj):
        return bool(obj.face_encoding)


class EmployeeSerializer(serializers.ModelSerializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source="location", queryset=Location.objects.filter(is_deleted=False)
    )
    location_name = serializers.CharField(source="location.name", read_only=True)
    photo_data = serializers.SerializerMethodField()
    has_face_encoding = serializers.SerializerMethodField()
    payslip_field_config_id = serializers.UUIDField(source="payslip_field_config.id", read_only=True, allow_null=True)
    payslip_field_config_name = serializers.CharField(source="payslip_field_config.config_name", read_only=True, allow_null=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "name",
            "location_id",
            "location_name",
            "base_salary",
            "deduction_per_day",
            "gross_salary",
            "payslip_field_config_id",
            "payslip_field_config_name",
            "employee_code",
            "department",
            "designation",
            "experience_years",
            "joining_date",
            "bank_account_number",
            "ifsc_code",
            "bank_name",
            "pan_number",
            "aadhaar_number",
            "uan_number",
            "esi_number",
            "email",
            "phone",
            "address",
            "photo_data",
            "has_face_encoding",
        ]

    def get_photo_data(self, obj):
        if obj.photo:
            return "data:image/jpeg;base64," + base64.b64encode(obj.photo).decode("utf-8")
        return None

    def get_has_face_encoding(self, obj):
        return bool(obj.face_encoding)


class EmployeeUpdateSerializer(serializers.ModelSerializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source="location",
        queryset=Location.objects.filter(is_deleted=False),
        allow_null=True,
        required=False,
    )
    payslip_field_config_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Employee
        fields = [
            "name",
            "location_id",
            "gross_salary",
            "payslip_field_config_id",
            "employee_code",
            "department",
            "designation",
            "experience_years",
            "joining_date",
            "bank_account_number",
            "ifsc_code",
            "bank_name",
            "pan_number",
            "aadhaar_number",
            "uan_number",
            "esi_number",
            "email",
            "phone",
            "address",
        ]
        extra_kwargs = {"name": {"required": False}}

    def update(self, instance, validated_data):
        location = validated_data.pop("location", None)
        if location is not None:
            instance.location = location
        
        payslip_field_config_id = validated_data.pop("payslip_field_config_id", None)
        if payslip_field_config_id is not None:
            try:
                from payslip.models import PayslipFieldConfig
                instance.payslip_field_config = PayslipFieldConfig.objects.get(pk=payslip_field_config_id, is_deleted=False)
            except (ImportError, PayslipFieldConfig.DoesNotExist):
                instance.payslip_field_config = None
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class ShiftSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    location_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    
    class Meta:
        model = Shift
        fields = [
            "id",
            "shift_name",
            "start_time",
            "end_time",
            "grace_timing",
            "location",
            "location_id",
            "location_name",
            "created_on",
            "created_by",
            "modified_on",
            "modified_by",
            "is_deleted",
            "deleted_by",
        ]
        read_only_fields = [
            "id",
            "created_on",
            "modified_on",
            "created_by",
            "modified_by",
            "deleted_by",
            "location_name",
        ]
    
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
        
        return representation


class SiteSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    shift_ids = serializers.PrimaryKeyRelatedField(source='shifts', many=True, queryset=Shift.objects.all(), required=False)
    shifts = ShiftSerializer(many=True, read_only=True)

    class Meta:
        model = Site
        fields = [
            'id', 'site_name', 'latitude', 'longitude', 'location', 'location_name',
            'shift_ids', 'shifts', 'distance_meters', 'created_on', 'created_by',
            'modified_on', 'modified_by', 'is_deleted', 'deleted_by'
        ]
        read_only_fields = ['id', 'created_on', 'modified_on', 'created_by', 'modified_by', 'deleted_by']

    def create(self, validated_data):
        shifts = validated_data.pop('shifts', [])
        site = Site.objects.create(**validated_data)
        if shifts:
            site.shifts.set(shifts)
        return site

    def update(self, instance, validated_data):
        shifts = validated_data.pop('shifts', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if shifts is not None:
            instance.shifts.set(shifts)
        return instance


class AssignmentSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.name', read_only=True)
    shift_name = serializers.CharField(source='shift.shift_name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    shift = serializers.PrimaryKeyRelatedField(queryset=Shift.objects.all(), allow_null=True, required=False)

    class Meta:
        model = Assignment
        fields = '__all__'
        read_only_fields = ['id', 'created_on', 'modified_on', 'created_by', 'modified_by', 'deleted_by']


class UserSiteSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.name', read_only=True)
    site_name = serializers.CharField(source='site.site_name', read_only=True)

    class Meta:
        model = UserSite
        fields = '__all__'
        read_only_fields = ['id', 'created_on', 'modified_on', 'assigned_on', 'created_by', 'modified_by', 'deleted_by', 'assigned_by']
