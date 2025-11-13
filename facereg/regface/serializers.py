import base64

from django.contrib.auth.hashers import make_password
from rest_framework import serializers

from .models import Employee, Location, User


class FaceUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()


class EmployeeRegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    location_id = serializers.PrimaryKeyRelatedField(
        source="location", queryset=Location.objects.filter(is_deleted=False)
    )
    face_image = serializers.ImageField()
    profile_photo = serializers.ImageField(required=False, allow_null=True)

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

    class Meta:
        model = Employee
        fields = [
            "id",
            "name",
            "location_id",
            "location_name",
            "base_salary",
            "deduction_per_day",
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

    class Meta:
        model = Employee
        fields = ["name", "location_id"]
        extra_kwargs = {"name": {"required": False}}

    def update(self, instance, validated_data):
        location = validated_data.pop("location", None)
        if location is not None:
            instance.location = location
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance
