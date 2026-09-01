from django.test import RequestFactory, SimpleTestCase
from rest_framework.request import Request

from subapps.pagination import OptionalPageNumberPagination
from subapps.permissions.microservice_permissions import CachingMixin


class OptionalPageNumberPaginationTests(SimpleTestCase):
    def test_legacy_request_remains_an_unpaginated_collection(self):
        request = Request(RequestFactory().get("/stock_api/locations/"))

        page = OptionalPageNumberPagination().paginate_queryset(range(25), request)

        self.assertIsNone(page)

    def test_paged_request_returns_the_shared_navigation_envelope(self):
        request = Request(RequestFactory().get("/stock_api/locations/?page=2&page_size=20"))
        pagination = OptionalPageNumberPagination()

        page = pagination.paginate_queryset(range(25), request)
        response = pagination.get_paginated_response(list(page))

        self.assertEqual(response.data["count"], 25)
        self.assertEqual(response.data["page"], 2)
        self.assertEqual(response.data["page_size"], 20)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertEqual(response.data["results"], list(range(20, 25)))


class CacheKeyQueryParameterTests(SimpleTestCase):
    def test_repeated_query_values_produce_distinct_cache_keys(self):
        view = CachingMixin()
        first_scope = Request(
            RequestFactory().get("/stock_api/balances/?structural_location_ids=one&structural_location_ids=two")
        )
        second_scope = Request(
            RequestFactory().get("/stock_api/balances/?structural_location_ids=three&structural_location_ids=two")
        )

        self.assertNotEqual(
            view._generate_cache_key(first_scope),
            view._generate_cache_key(second_scope),
        )
