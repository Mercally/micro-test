import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

import yaml

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import frontend


class FrontendDeploymentTest(unittest.TestCase):
    def test_s3_template_exposes_static_files_only(self):
        body = frontend.template()
        resources = body["Resources"]
        bucket = resources["FrontendBucket"]["Properties"]
        self.assertEqual(bucket["BucketName"], "micro-test-local-frontend")
        package = json.loads((frontend.ROOT / "frontend" / "package.json").read_text())
        self.assertIn(
            "/" + bucket["BucketName"] + "/",
            package["scripts"]["build:localstack"])
        self.assertNotIn("WebsiteConfiguration", bucket)
        policy = resources["FrontendReadPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]
        self.assertEqual(policy["Action"], ["s3:GetObject"])
        self.assertEqual(policy["Principal"], "*")
        self.assertEqual(
            policy["Resource"],
            {"Fn::Sub": "${FrontendBucket.Arn}/*"})
        self.assertEqual(
            yaml.safe_load((HERE / "templates" / "frontend.yaml").read_text()), body)

    def test_status_checks_the_existing_lambda_authorizer_and_routes(self):
        lab = Mock()
        lab.outputs.return_value = {"ApiId": "api123"}
        api = Mock()
        api.get_authorizers.return_value = {"items": [{
            "id": "auth123", "name": "purchase-jwt", "type": "TOKEN",
            "identitySource": "method.request.header.Authorization",
            "authorizerUri": "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:000000000000:function:micro-test-local-authorizer/invocations",
        }]}
        api.get_resources.return_value = {"items": [
            {"id": "r1", "path": "/purchases"},
            {"id": "r2", "path": "/purchases/{saleId}"},
            {"id": "r3", "path": "/purchases/{saleId}/retry"},
        ]}
        api.get_method.side_effect = [
            {"authorizationType": "CUSTOM", "authorizerId": "auth123"},
            {"authorizationType": "CUSTOM", "authorizerId": "auth123"},
            {"authorizationType": "CUSTOM", "authorizerId": "auth123"},
        ]
        lab.client.return_value = api
        self.assertEqual(frontend.verify_api_gateway(lab), "api123")
        self.assertEqual(api.get_method.call_count, 3)

    def test_status_rejects_an_unprotected_route(self):
        lab = Mock()
        lab.outputs.return_value = {"ApiId": "api123"}
        api = Mock()
        api.get_authorizers.return_value = {"items": [{
            "id": "auth123", "name": "purchase-jwt", "type": "TOKEN",
            "identitySource": "method.request.header.Authorization",
            "authorizerUri": "arn:aws:lambda:us-east-1:000000000000:function:micro-test-local-authorizer/invocations",
        }]}
        api.get_resources.return_value = {"items": [{"id": "r1", "path": "/purchases"}]}
        api.get_method.return_value = {"authorizationType": "NONE"}
        lab.client.return_value = api
        with self.assertRaisesRegex(ValueError, "not protected"):
            frontend.verify_api_gateway(lab)


if __name__ == "__main__":
    unittest.main()
