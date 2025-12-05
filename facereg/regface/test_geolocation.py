#!/usr/bin/env python
"""
Test script to verify geolocation functionality is working correctly.
Run this in Django shell: python manage.py shell < test_geolocation.py
"""

from regface.models import Location, AttendanceLog, Employee
from regface.geolocation import (
    calculate_distance,
    reverse_geocode,
    detect_location_from_coordinates
)
from decimal import Decimal

print("=" * 60)
print("GEOLOCATION IMPLEMENTATION TEST")
print("=" * 60)

# Test 1: Distance Calculation
print("\n[TEST 1] Distance Calculation")
print("-" * 60)
lat1, lon1 = 40.7128, -74.0060  # New York
lat2, lon2 = 40.7580, -73.9855  # Midtown Manhattan
distance = calculate_distance(lat1, lon1, lat2, lon2)
print(f"Distance between coordinates:")
print(f"  Point 1: {lat1}, {lon1}")
print(f"  Point 2: {lat2}, {lon2}")
print(f"  Result: {distance} km")
print(f"  Status: {'✅ PASS' if distance and 0 < distance < 10 else '❌ FAIL'}")

# Test 2: Reverse Geocoding
print("\n[TEST 2] Reverse Geocoding")
print("-" * 60)
print("Note: This requires internet connection (calls OpenStreetMap)")
try:
    geo_result = reverse_geocode(40.7128, -74.0060)
    if geo_result:
        print(f"Coordinates: 40.7128, -74.0060")
        print(f"Address: {geo_result.get('address')}")
        print(f"City: {geo_result.get('city')}")
        print(f"Status: ✅ PASS")
    else:
        print(f"Status: ⚠️  No result (network may be unavailable)")
except Exception as e:
    print(f"Status: ⚠️  Error: {str(e)}")

# Test 3: Location Detection
print("\n[TEST 3] Location Detection")
print("-" * 60)
locs = Location.objects.filter(is_deleted=False, latitude__isnull=False, longitude__isnull=False)
print(f"Active locations with coordinates: {locs.count()}")
for loc in locs:
    print(f"  - {loc.name}: ({loc.latitude}, {loc.longitude})")

if locs.count() > 0:
    # Test with a coordinate that should match or be near first location
    if locs.first().latitude and locs.first().longitude:
        test_lat = float(locs.first().latitude) + 0.001
        test_lon = float(locs.first().longitude) + 0.001
        detected_loc, dist = detect_location_from_coordinates(test_lat, test_lon, locs, radius_km=5.0)
        print(f"\nTest detection with coordinates near first location:")
        print(f"  Test coordinates: {test_lat}, {test_lon}")
        print(f"  Detected location: {detected_loc.name if detected_loc else 'None'}")
        print(f"  Distance: {dist} km" if dist else "  Distance: N/A")
        print(f"  Status: ✅ PASS" if detected_loc else "⚠️  No location detected within radius")
else:
    print("⚠️  No locations configured in database")
    print("  Please add locations via Django admin with latitude/longitude")

# Test 4: Check AttendanceLog model
print("\n[TEST 4] AttendanceLog Model")
print("-" * 60)
recent_logs = AttendanceLog.objects.all().order_by('-timestamp')[:3]
print(f"Recent attendance records: {AttendanceLog.objects.count()}")
for log in recent_logs:
    print(f"\n  Employee: {log.employee.name}")
    print(f"  Type: {log.type}")
    print(f"  Timestamp: {log.timestamp}")
    print(f"  Coordinates: ({log.latitude}, {log.longitude})" if log.latitude else "  Coordinates: Not captured")
    print(f"  Location: {log.location.name if log.location else 'Not detected'}")
    print(f"  Location Name: {log.location_name or 'N/A'}")
    print(f"  Location Address: {log.location_address or 'N/A'}")

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
print("\nNext steps:")
print("1. Add office locations with coordinates in Django admin")
print("2. Mark attendance from frontend with geolocation enabled")
print("3. Verify location details are captured in attendance records")
print("4. Review GEOLOCATION_IMPLEMENTATION.md for integration guide")
