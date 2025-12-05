"""
Geolocation utilities for attendance marking with location detection.

This module provides functions for:
1. Converting coordinates to location names (reverse geocoding)
2. Detecting which location an employee is marking attendance from
3. Calculating distance between coordinates
"""

import math
from decimal import Decimal
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import logging

logger = logging.getLogger(__name__)


def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate distance in kilometers between two coordinates using Haversine formula.
    
    Args:
        lat1, lon1: First coordinate (employee location)
        lat2, lon2: Second coordinate (reference location)
    
    Returns:
        Distance in kilometers
    """
    try:
        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)
        
        R = 6371  # Earth's radius in kilometers
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        distance = R * c
        
        return round(distance, 2)
    except (ValueError, TypeError) as e:
        logger.error(f"Error calculating distance: {e}")
        return None


def reverse_geocode(latitude, longitude):
    """
    Get location address from latitude and longitude using Nominatim (OpenStreetMap).
    
    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate
    
    Returns:
        Dict with 'address' and 'city' keys, or None if geocoding fails
    """
    try:
        if latitude is None or longitude is None:
            return None
            
        latitude = float(latitude)
        longitude = float(longitude)
        
        geolocator = Nominatim(user_agent="facereg_attendance")
        location = geolocator.reverse(f"{latitude}, {longitude}", language='en', timeout=5)
        
        if location:
            address = location.address
            # Extract city from address (usually after last comma)
            city = address.split(',')[-2].strip() if ',' in address else address
            
            return {
                'address': address,
                'city': city,
                'latitude': latitude,
                'longitude': longitude
            }
    except GeocoderTimedOut:
        logger.warning(f"Geocoder timeout for coordinates {latitude}, {longitude}")
    except GeocoderUnavailable:
        logger.warning(f"Geocoder unavailable for coordinates {latitude}, {longitude}")
    except Exception as e:
        logger.error(f"Error in reverse geocoding: {e}")
    
    return None


def detect_location_from_coordinates(latitude, longitude, location_objects, radius_km=0.05):
    """
    Detect which Location the employee is marking attendance from based on coordinates.
    Uses the closest location within the specified radius (default: 50 meters).
    
    Args:
        latitude: Employee's current latitude
        longitude: Employee's current longitude
        location_objects: QuerySet or list of Location objects with 'latitude' and 'longitude' fields
        radius_km: Maximum distance in kilometers to consider (default: 0.05 km = 50 meters)
    
    Returns:
        Tuple of (Location object, distance_in_km) or (None, None) if no location within radius
    """
    try:
        if latitude is None or longitude is None:
            logger.warning("Latitude or longitude is None")
            return None, None
        
        latitude = float(latitude)
        longitude = float(longitude)
        
        closest_location = None
        closest_distance = radius_km
        
        for location in location_objects:
            if not hasattr(location, 'latitude') or not hasattr(location, 'longitude'):
                continue
                
            if location.latitude is None or location.longitude is None:
                continue
            
            distance = calculate_distance(latitude, longitude, location.latitude, location.longitude)
            
            if distance is not None and distance <= closest_distance:
                closest_distance = distance
                closest_location = location
        
        if closest_location:
            logger.info(f"Detected location: {closest_location.name} at {closest_distance}km distance")
            return closest_location, closest_distance
        
        logger.info(f"No location found within {radius_km}km radius")
        return None, None
        
    except Exception as e:
        logger.error(f"Error in location detection: {e}")
        return None, None
