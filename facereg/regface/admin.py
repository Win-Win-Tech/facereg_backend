from django.contrib import admin

from .models import AuthToken, Employee, Location, PayrollRecord, User


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "is_deleted", "created_at", "updated_at")
    list_filter = ("is_deleted",)
    search_fields = ("name",)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "name",
        "role",
        "location",
        "is_active",
        "is_deleted",
        "created_at",
    )
    list_filter = ("role", "is_active", "is_deleted")
    search_fields = ("email", "name")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "name", "password")}),
        ("Details", {"fields": ("role", "location", "is_active", "is_deleted")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "base_salary", "deduction_per_day")
    list_filter = ("location",)
    search_fields = ("name",)


@admin.register(PayrollRecord)
class PayrollRecordAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "month",
        "present_days",
        "absent_days",
        "net_pay",
        "generated_on",
    )
    list_filter = ("month", "employee__location")


@admin.register(AuthToken)
class AuthTokenAdmin(admin.ModelAdmin):
    list_display = ("key", "user", "created_at")
    search_fields = ("key", "user__email")
