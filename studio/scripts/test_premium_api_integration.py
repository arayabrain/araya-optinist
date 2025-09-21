#!/usr/bin/env python3
"""
Premium API Integration Tests

RUNTIME ENVIRONMENT:
✅ Can run locally (with mocked dependencies)
✅ Can run on cloud (with mocked dependencies)
⚠️ Requires studio app modules to be available

Tests the critical premium management API endpoints,
especially the new heartbeat endpoint.
These tests verify that our fixes for the premium management system work end-to-end.
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestPremiumAPIIntegration:
    """Test suite for Premium API endpoints"""

    def setup_method(self):
        """Setup test environment"""
        self.test_user_id = 12345
        self.test_user_data = {
            "id": self.test_user_id,
            "email": "test@example.com",
            "subscription_type": "premium",
            "subscription_plan_name": "Premium",
            "subscription_status": "Premium",
        }

    @patch("studio.app.common.core.premium.premium_assignment_service.boto3.client")
    @patch("studio.app.common.core.premium.premium_assignment_service.os.environ.get")
    def test_heartbeat_endpoint_success(self, mock_environ, mock_boto3):
        """Test the new heartbeat endpoint works correctly for premium users"""

        # Mock environment variables
        mock_environ.side_effect = lambda key, default=None: {
            "MYSQL_SERVER": "localhost",
            "DATABASE_URL": "mysql://localhost/test",
        }.get(key, default)

        # Import after mocking
        from studio.app.common.core.premium.premium_assignment_service import (
            premium_assignment_service,
        )

        # Test heartbeat method directly (simulates local development)
        result = premium_assignment_service.update_user_activity(self.test_user_id)

        # Verify response structure
        assert isinstance(result, dict), "Heartbeat should return dict"
        assert "success" in result, "Response should include success field"
        assert "message" in result, "Response should include message field"
        assert "user_id" in result, "Response should include user_id field"

        print("✅ Heartbeat endpoint structure test passed")

    def test_heartbeat_endpoint_with_fastapi_mock(self):
        """Test heartbeat endpoint through FastAPI router simulation"""

        # Mock the FastAPI dependencies and request handling
        with patch(
            "studio.app.common.routers.users_me.get_current_user"
        ) as mock_get_user, patch(
            "studio.app.common.routers.users_me.premium_assignment_service"
        ) as mock_service:
            # Setup mocks
            mock_get_user.return_value = MagicMock(**self.test_user_data)
            mock_service.update_user_activity.return_value = {
                "success": True,
                "message": "Activity updated successfully",
                "timestamp": time.time(),
                "user_id": self.test_user_id,
            }

            # Import the endpoint function
            # Create a mock request context
            import asyncio

            from studio.app.common.routers.users_me import send_premium_heartbeat

            async def run_test():
                # Call the endpoint
                result = await send_premium_heartbeat(mock_get_user.return_value)

                # Verify the response
                assert isinstance(result, dict), "Endpoint should return dict"
                assert result["message"] == "Activity updated successfully"
                assert result["updated"] is True
                assert result["user_id"] == self.test_user_id
                assert result["user_tier"] == "premium"
                assert result["assignment_active"] is True

                return result

            # Run the async test
            if hasattr(asyncio, "run"):
                result = asyncio.run(run_test())
            else:
                # Python 3.6 compatibility
                loop = asyncio.get_event_loop()
                result = loop.run_until_complete(run_test())

            print("✅ FastAPI heartbeat endpoint test passed")
            print(f"   Response: {json.dumps(result, indent=2)}")

    def test_heartbeat_endpoint_non_premium_user(self):
        """Test heartbeat endpoint with non-premium user"""

        non_premium_user = {
            **self.test_user_data,
            "subscription_type": "free",
            "subscription_plan_name": "Free",
            "subscription_status": "Free",
        }

        with patch(
            "studio.app.common.routers.users_me.get_current_user"
        ) as mock_get_user:
            mock_get_user.return_value = MagicMock(**non_premium_user)

            import asyncio

            from studio.app.common.routers.users_me import send_premium_heartbeat

            async def run_test():
                result = await send_premium_heartbeat(mock_get_user.return_value)

                # Verify non-premium response
                assert result["user_tier"] == "free"
                assert result["assignment_active"] is False
                assert result["updated"] is False
                assert "non-premium user" in result["message"]

                return result

            if hasattr(asyncio, "run"):
                result = asyncio.run(run_test())
            else:
                loop = asyncio.get_event_loop()
                result = loop.run_until_complete(run_test())

            print("✅ Non-premium user heartbeat test passed")
            print(f"   Response: {json.dumps(result, indent=2)}")

    def test_heartbeat_error_handling(self):
        """Test heartbeat endpoint error handling"""

        with patch(
            "studio.app.common.routers.users_me.get_current_user"
        ) as mock_get_user, patch(
            "studio.app.common.routers.users_me.premium_assignment_service"
        ) as mock_service:
            # Setup mocks
            mock_get_user.return_value = MagicMock(**self.test_user_data)
            mock_service.update_user_activity.side_effect = Exception(
                "Database connection failed"
            )

            import asyncio

            from studio.app.common.routers.users_me import send_premium_heartbeat

            async def run_test():
                result = await send_premium_heartbeat(mock_get_user.return_value)

                # Verify error response (should not fail, just warn)
                assert result["updated"] is False
                assert result["user_tier"] == "premium"
                assert result["assignment_active"] is False
                assert "warnings" in result["message"]
                assert "error" in result

                return result

            if hasattr(asyncio, "run"):
                result = asyncio.run(run_test())
            else:
                loop = asyncio.get_event_loop()
                result = loop.run_until_complete(run_test())

            print("✅ Heartbeat error handling test passed")
            print(f"   Error response: {json.dumps(result, indent=2)}")

    def test_assign_release_status_endpoints(self):
        """Test the core assign/release/status endpoints work"""

        with patch(
            "studio.app.common.routers.users_me.get_current_user"
        ) as mock_get_user, patch(
            "studio.app.common.routers.users_me.premium_assignment_service"
        ) as mock_service:
            # Setup mocks
            mock_get_user.return_value = MagicMock(**self.test_user_data)

            # Mock assignment service responses
            mock_service.assign_premium_user.return_value = {
                "success": True,
                "message": "Assignment successful",
                "instance_id": "i-test123",
            }

            mock_service.release_premium_user.return_value = {
                "success": True,
                "message": "Release successful",
                "released_instance": "i-test123",
            }

            mock_service.get_premium_user_status.return_value = {
                "instance_id": "i-test123",
                "status": "active",
            }

            # Import endpoints
            import asyncio

            from studio.app.common.routers.users_me import (
                assign_premium_instance,
                get_premium_assignment_status,
                release_premium_instance,
            )

            async def run_tests():
                # Test assignment
                assign_result = await assign_premium_instance(
                    mock_get_user.return_value
                )
                assert assign_result["assigned"] is True
                assert assign_result["instance_id"] == "i-test123"

                # Test status
                status_result = await get_premium_assignment_status(
                    mock_get_user.return_value
                )
                assert status_result["is_premium"] is True
                assert status_result["assignment"]["instance_id"] == "i-test123"

                # Test release
                release_result = await release_premium_instance(
                    mock_get_user.return_value
                )
                assert release_result["released"] is True

                return {
                    "assign": assign_result,
                    "status": status_result,
                    "release": release_result,
                }

            if hasattr(asyncio, "run"):
                asyncio.run(run_tests())
            else:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(run_tests())

            print("✅ Assign/Status/Release endpoints test passed")
            print("   All endpoints responded correctly")


def run_api_integration_tests():
    """Run all API integration tests"""

    print("🧪 Starting Premium API Integration Tests")
    print("=" * 60)

    test_suite = TestPremiumAPIIntegration()
    test_suite.setup_method()

    tests = [
        ("Heartbeat Endpoint Success", test_suite.test_heartbeat_endpoint_success),
        (
            "Heartbeat FastAPI Integration",
            test_suite.test_heartbeat_endpoint_with_fastapi_mock,
        ),
        (
            "Heartbeat Non-Premium User",
            test_suite.test_heartbeat_endpoint_non_premium_user,
        ),
        ("Heartbeat Error Handling", test_suite.test_heartbeat_error_handling),
        (
            "Assign/Release/Status Endpoints",
            test_suite.test_assign_release_status_endpoints,
        ),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            print(f"\n🔬 Running: {test_name}")
            test_func()
            passed += 1
            print(f"   ✅ PASSED: {test_name}")
        except Exception as e:
            failed += 1
            print(f"   ❌ FAILED: {test_name}")
            print(f"      Error: {str(e)}")
            import traceback

            print(f"      Details: {traceback.format_exc()}")

    print(f"\n📊 Test Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All API integration tests passed!")
        return True
    else:
        print("⚠️ Some tests failed - check the errors above")
        return False


if __name__ == "__main__":
    try:
        success = run_api_integration_tests()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Test runner failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
