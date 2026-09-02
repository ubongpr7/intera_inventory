from django.contrib import admin
from django.http import JsonResponse
from django.db import connection
from django.urls import path,include
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from schema_graph.views import Schema

schema_view = get_schema_view(
   openapi.Info(
      title="Intera Inventory API",
      default_version='v1',
      description="Inventory, warehouse, stock, purchasing, and fulfillment APIs for Intera.",
      contact=openapi.Contact(email="platform@intera.technology"),
   ),
   public=True,
   permission_classes=(permissions.AllowAny,),
)


def healthz(_request):
    return JsonResponse({"status": "ok"})


def readyz(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return JsonResponse(
            {"status": "error", "database": "unavailable", "error": type(exc).__name__},
            status=503,
        )
    return JsonResponse({"status": "ok", "database": "available"})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz/', healthz, name='healthz'),
    path('readyz/', readyz, name='readyz'),
    # djoser urls
    # path('auth-api/', include('djoser.urls')),
    # path('', include('djoser.urls.jwt')),

    #  api endpoints docs
    path('swagger<format>/', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path("schema/", Schema.as_view()),

    # db sync

    path('inventory_api/', include("mainapps.inventory.urls",)),
    path('company_api/', include("mainapps.company.urls",)),
    path('order_api/', include("mainapps.orders.urls",)),
    path('stock_api/', include("mainapps.stock.urls",)),

    # path("mcp_server/", include('mcp_server.urls')),

]
