from rest_framework import serializers

class FaceUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()

