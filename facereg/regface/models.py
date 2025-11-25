import secrets
import uuid
from decimal import Decimal
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models


class Shift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shift_name = models.CharField(max_length=100, null=True, blank=True, default=None)
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_timing = models.IntegerField(default=30)  # minutes
    created_on = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_shifts"
    )
    modified_on = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="modified_shifts"
    )
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_shifts"
    )

    def __str__(self):
        return f"{self.shift_name} ({self.start_time} - {self.end_time})"


class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class User(models.Model):
    class Role(models.TextChoices):
        SUPERADMIN = "superadmin", "Super Admin"
        ADMIN = "admin", "Admin"

    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    location = models.ForeignKey("Location", on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    password = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if self.role == self.Role.ADMIN and not self.location_id:
            raise ValidationError("Admin users must be assigned to a location.")
        if self.role == self.Role.SUPERADMIN and self.location_id:
            raise ValidationError("Super admin users cannot be assigned to a location.")

    def set_password(self, raw_password: str):
        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password(raw_password, self.password)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def __str__(self):
        return f"{self.name} ({self.email})"


class Employee(models.Model):
    location = models.ForeignKey("Location", on_delete=models.CASCADE, related_name="employees", null=True, blank=True)
    name = models.CharField(max_length=100)
    face_encoding = models.BinaryField()
    photo = models.BinaryField(null=True, blank=True)
    base_salary = models.DecimalField(max_digits=10, decimal_places=2, default=30000)
    deduction_per_day = models.DecimalField(max_digits=10, decimal_places=2, default=500)

    def __str__(self):
        return self.name


class Site(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site_name = models.CharField(max_length=255)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    location = models.ForeignKey("Location", on_delete=models.CASCADE, related_name="sites")
    distance_meters = models.DecimalField(max_digits=6, decimal_places=2)
    created_on = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, related_name="created_sites")
    modified_on = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, related_name="modified_sites")
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True, related_name="deleted_sites")

    def __str__(self):
        return self.site_name


class AttendanceLog(models.Model):
    CHECKIN = "checkin"
    CHECKOUT = "checkout"
    TYPE_CHOICES = [(CHECKIN, "Check-in"), (CHECKOUT, "Check-out")]

    employee = models.ForeignKey("Employee", on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=CHECKIN)
    shift = models.ForeignKey("Shift", on_delete=models.SET_NULL, null=True, blank=True)
    site = models.ForeignKey("Site", on_delete=models.SET_NULL, null=True, blank=True)
    location = models.ForeignKey("Location", on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.employee.name} - {self.type} at {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"


class PayrollRecord(models.Model):
    employee = models.ForeignKey("Employee", on_delete=models.CASCADE)
    month = models.CharField(max_length=7)  # Format: YYYY-MM
    present_days = models.IntegerField(default=0)
    absent_days = models.IntegerField(default=0)
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    deduction_per_day = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pf_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    esi_deduction = models.DecimalField(max_digits=10, decimal_places=2)
    net_pay = models.DecimalField(max_digits=10, decimal_places=2)
    generated_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_on"]

    def __str__(self):
        return f"Payroll {self.month} - {self.employee.name}"


class AuthToken(models.Model):
    key = models.CharField(max_length=40, primary_key=True, editable=False)
    user = models.ForeignKey("User", related_name="auth_tokens", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = secrets.token_hex(20)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Token for {self.user.email}"


class Assignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE)
    location = models.ForeignKey("Location", on_delete=models.CASCADE)
    shift = models.ForeignKey("Shift", on_delete=models.CASCADE)
    created_on = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, related_name="created_assignments")
    modified_on = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, related_name="modified_assignments")
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True, related_name="deleted_assignments")

    def __str__(self):
        return f"{self.user.name} - {self.location.name} - {self.shift}"


class UserSite(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE)
    site = models.ForeignKey("Site", on_delete=models.CASCADE)
    assigned_on = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, related_name="assigned_user_sites")
    created_on = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, related_name="created_user_sites")
    modified_on = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, related_name="modified_user_sites")
    is_deleted = models.BooleanField(default=False)   # <-- this was cut off
    deleted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True, related_name="deleted_user_sites")

    def __str__(self):
        return f"{self.user.name} - {self.site.site_name}"
