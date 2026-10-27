from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.models import User


class EmailUniquenessMixin:
    """Case-insensitive email uniqueness (the DB column stores lower-case)."""

    def validate_email(self, value):
        value = value.strip().lower()
        clash = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value


class UserSerializer(EmailUniquenessMixin, serializers.ModelSerializer):
    """The caller's own profile."""

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "date_joined",
        ]
        read_only_fields = ["id", "username", "role", "date_joined"]
        extra_kwargs = {"email": {"validators": []}}


class RegisterSerializer(EmailUniquenessMixin, serializers.ModelSerializer):
    """Public sign-up. Always creates a standard user, never an admin."""

    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    class Meta:
        model = User
        fields = ["username", "email", "password", "first_name", "last_name"]
        extra_kwargs = {"email": {"validators": []}}

    def validate(self, attrs):
        probe = User(username=attrs.get("username", ""), email=attrs.get("email", ""))
        try:
            validate_password(attrs["password"], user=probe)
        except Exception as exc:  # DjangoValidationError
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(role=User.Role.USER, **validated_data)

    def to_representation(self, instance):
        return UserSerializer(instance, context=self.context).data


class AdminUserSerializer(EmailUniquenessMixin, serializers.ModelSerializer):
    """Full user management for administrators."""

    password = serializers.CharField(
        write_only=True,
        required=False,
        style={"input_type": "password"},
        trim_whitespace=False,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "is_active",
            "password",
            "date_joined",
        ]
        read_only_fields = ["id", "date_joined"]
        extra_kwargs = {"email": {"validators": []}}

    def validate(self, attrs):
        password = attrs.get("password")
        if self.instance is None and not password:
            raise serializers.ValidationError({"password": "This field is required."})
        if password:
            try:
                validate_password(password, user=self.instance)
            except Exception as exc:  # DjangoValidationError
                raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save(update_fields=["password"])
        return instance
