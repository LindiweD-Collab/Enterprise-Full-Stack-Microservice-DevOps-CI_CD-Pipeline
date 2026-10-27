from django.urls import path
from drf_spectacular.utils import extend_schema
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.views import MeView, RegisterView, UserViewSet

router = SimpleRouter()
router.register("users", UserViewSet, basename="user")

TokenObtainPairView = extend_schema(tags=["auth"], summary="Obtain a JWT pair")(
    TokenObtainPairView
)
TokenRefreshView = extend_schema(tags=["auth"], summary="Refresh an access token")(
    TokenRefreshView
)

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/token/", TokenObtainPairView.as_view(), name="token-obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
] + router.urls
