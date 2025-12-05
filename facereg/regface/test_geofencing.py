#!/usr/bin/env python
"""
Test script to verify 50-meter geofencing implementation
Run this in Django shell: python manage.py shell < test_geofencing.py
"""

from regface.models import Location, AttendanceLog, Employee
from regface.geolocation import (
    calculate_distance,
    detect_location_from_coordinates
)
from decimal import Decimal

print("=" * 70)
print("50-METER GEOFENCING TEST SUITE")
print("=" * 70)

# Test 1: Distance Calculation in Meters
print("\n[TEST 1] Distance Calculation (Haversine Formula)")
print("-" * 70)

test_cases = [
    {
        "name": "Same location",
        "lat1": 40.7128, "lon1": -74.0060,
        "lat2": 40.7128, "lon2": -74.0060,
        "expected_meters": 0
    },
    {
        "name": "10 meters apart",
        "lat1": 40.712800, "lon1": -74.006000,
        "lat2": 40.712891, "lon2": -74.006000,
        "expected_meters": 10
    },
    {
        "name": "50 meters apart (geofence boundary)",
        "lat1": 40.712800, "lon1": -74.006000,
        "lat2": 40.713450, "lon2": -74.006000,
        "expected_meters": 50
    },
    {
        "name": "100 meters apart (outside geofence)",
        "lat1": 40.712800, "lon1": -74.006000,
        "lat2": 40.714100, "lon2": -74.006000,
        "expected_meters": 100
    },
]

for test in test_cases:
    distance_km = calculate_distance(test["lat1"], test["lon1"], test["lat2"], test["lon2"])
    distance_m = distance_km * 1000 if distance_km else 0
    status = "✅" if abs(distance_m - test["expected_meters"]) < 5 else "❌"
    print(f"{status} {test['name']}: {distance_m:.1f}m (expected ~{test['expected_meters']}m)")

# Test 2: Geofencing with 50m Radius
print("\n[TEST 2] 50-Meter Geofencing Detection")
print("-" * 70)

# Create test locations if they don't exist
test_locations = [
    {"name": "Test Office A", "lat": 40.7128, "lon": -74.0060},
    {"name": "Test Office B", "lat": 34.0522, "lon": -118.2437},
]

for loc_data in test_locations:
    existing = Location.objects.filter(name=loc_data["name"]).first()
    if not existing:
        Location.objects.create(
            name=loc_data["name"],
            latitude=Decimal(str(loc_data["lat"])),
            longitude=Decimal(str(loc_data["lon"])),
            address=f"{loc_data['name']} Address"
        )
        print(f"✓ Created test location: {loc_data['name']}")
    else:
        print(f"✓ Using existing location: {loc_data['name']}")

# Get locations with coordinates
locations = Location.objects.filter(
    name__in=["Test Office A", "Test Office B"],
    latitude__isnull=False,
    longitude__isnull=False
)

print(f"\nAvailable test locations: {locations.count()}")

# Test detection scenarios
scenarios = [
    {
        "description": "Within 50m geofence",
        "lat": 40.712800,
        "lon": -74.006000,
        "should_detect": True
    },
    {
        "description": "At boundary (50m)",
        "lat": 40.713450,
        "lon": -74.006000,
        "should_detect": True
    },
    {
        "description": "Outside geofence (100m)",
        "lat": 40.714100,
        "lon": -74.006000,
        "should_detect": False
    },
    {
        "description": "Far away (different city)",
        "lat": 34.0522,
        "lon": -118.2437,
        "should_detect": True  # Should detect Office B
    },
]

for scenario in scenarios:
    detected_loc, distance_km = detect_location_from_coordinates(
        scenario["lat"], scenario["lon"], locations, radius_km=0.05
    )
    distance_m = (distance_km * 1000) if distance_km else None
    detected = detected_loc is not None
    status = "✅" if detected == scenario["should_detect"] else "❌"
    
    print(f"\n{status} {scenario['description']}")
    print(f"   Coordinates: {scenario['lat']}, {scenario['lon']}")
    if detected:
        print(f"   Detected: {detected_loc.name}")
        print(f"   Distance: {distance_m:.1f}m")
    else:
        print(f"   Not detected (no location within 50m radius)")

# Test 3: AttendanceLog with Geofencing
print("\n\n[TEST 3] Attendance Records with Geofencing")
print("-" * 70)

recent_logs = AttendanceLog.objects.all().order_by('-timestamp')[:5]

if recent_logs.count() > 0:
    print(f"Recent attendance records: {recent_logs.count()}\n")
    for log in recent_logs:
        distance_m = None
        if log.latitude and log.longitude and log.location:
            distance_km = calculate_distance(
                float(log.latitude), float(log.longitude),
                float(log.location.latitude), float(log.location.longitude)
            )
            distance_m = distance_km * 1000 if distance_km else None
        
        status = "✅ IN GEOFENCE" if distance_m and distance_m <= 50 else "⚠️  OUTSIDE GEOFENCE" if distance_m else "❓ NO LOCATION"
        
        print(f"Employee: {log.employee.name}")
        print(f"Type: {log.type.upper()}")
        print(f"Timestamp: {log.timestamp}")
        if log.location:
            print(f"Location: {log.location.name}")
            print(f"Distance: {distance_m:.1f}m {status}")
        else:
            print(f"Location: Not detected")
        print("-" * 50)
else:
    print("⚠️  No attendance records found. Mark attendance first to test.")

# Test 4: Geofencing Statistics
print("\n[TEST 4] Geofencing Statistics")
print("-" * 70)

all_logs = AttendanceLog.objects.all()
logs_with_location = all_logs.filter(location__isnull=False)
logs_without_location = all_logs.exclude(location__isnull=False)

print(f"Total attendance records: {all_logs.count()}")
print(f"Records with detected location: {logs_with_location.count()}")
print(f"Records without detected location: {logs_without_location.count()}")

# Calculate geofence compliance
in_geofence = 0
outside_geofence = 0

for log in all_logs:
    if log.latitude and log.longitude and log.location and log.location.latitude and log.location.longitude:
        distance_km = calculate_distance(
            float(log.latitude), float(log.longitude),
            float(log.location.latitude), float(log.location.longitude)
        )
        distance_m = (distance_km * 1000) if distance_km else None
        if distance_m and distance_m <= 50:
            in_geofence += 1
        else:
            outside_geofence += 1

if in_geofence + outside_geofence > 0:
    compliance_rate = (in_geofence / (in_geofence + outside_geofence)) * 100
    print(f"\nGeofence Compliance:")
    print(f"  Within 50m radius: {in_geofence} ({compliance_rate:.1f}%)")
    print(f"  Outside 50m radius: {outside_geofence} ({100-compliance_rate:.1f}%)")

print("\n" + "=" * 70)
print("TEST COMPLETE")
print("=" * 70)
print("\nKey Metrics:")
print(f"  • Geofence Radius: 50 meters")
print(f"  • Locations Configured: {locations.count()}")
print(f"  • Attendance Records: {all_logs.count()}")
print(f"  • In Geofence: {in_geofence if in_geofence + outside_geofence > 0 else 'N/A'}")
print(f"  • Outside Geofence: {outside_geofence if in_geofence + outside_geofence > 0 else 'N/A'}")

print("\nNext Steps:")
print("1. Define office locations with GPS coordinates in admin")
print("2. Employees mark attendance with geolocation enabled")
print("3. Monitor distance_meters in API responses")
print("4. Review attendance records to verify geofence detection")
