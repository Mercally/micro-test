#!/usr/bin/env python3
"""Read deployed local resources back through their owning service APIs."""
import argparse
import json
import sys
from common import ROOT, PREFIX, Lab, save, endpoint_url
from lab import FILES, suffix, owned_pipe


def audit(lab):
    result={"localstack":lab.info,"endpoint":lab.endpoint,"persistence":lab.health.get("features"),"stacks":{},"apiObservations":{}}
    for path in FILES:
        name=PREFIX+suffix(path); s=lab.stack(name)
        if not s:
            result["stacks"][name]="ABSENT"
            continue
        resources=lab.client("cloudformation").list_stack_resources(StackName=name)["StackResourceSummaries"]
        result["stacks"][name]={"status":s["StackStatus"],"resources":[{k:r.get(k) for k in ("LogicalResourceId","ResourceType","ResourceStatus","PhysicalResourceId")} for r in resources]}
    observed=result["apiObservations"]
    ec2=lab.client("ec2"); network=lab.outputs("network")
    subnets=ec2.describe_subnets(Filters=[{"Name":"vpc-id","Values":[network["VpcId"]]}])["Subnets"]
    routes=ec2.describe_route_tables(Filters=[{"Name":"vpc-id","Values":[network["VpcId"]]}])["RouteTables"]
    associated={a["SubnetId"] for r in routes for a in r["Associations"] if "SubnetId" in a}
    if len(subnets)!=8 or {s["SubnetId"] for s in subnets} != associated:
        raise AssertionError("Eight subnets and route-table associations expected")
    observed["network"]={"subnetCount":len(subnets),"associatedSubnetCount":len(associated),"note":"Metadata only. Docker traffic does not prove VPC/SG enforcement."}
    groups=ec2.describe_security_groups(GroupIds=[network["DatabaseSG"],network["ClientSG"],network["ClusterSG"]])["SecurityGroups"]
    observed["securityGroups"]=[{"id":g["GroupId"],"ingress":g["IpPermissions"]} for g in groups]
    db=lab.client("rds").describe_db_instances(DBInstanceIdentifier=PREFIX+"database")["DBInstances"][0]
    observed["rds"]={k:db.get(k) for k in ("DBInstanceStatus","Engine","EngineVersion","Endpoint","DBSubnetGroup","DBParameterGroups","VpcSecurityGroups")}
    observed["rds"]["sqlVerified"]=False
    secret=json.loads(lab.client("secretsmanager").get_secret_value(SecretId=lab.outputs("database")["SecretArn"])["SecretString"])
    if secret.get("username")!="docs" or secret.get("dbname")!="docs":raise AssertionError("Local secret contract mismatch")
    observed["secret"]="readable; local dummy credentials omitted"
    observed["ecr"]=lab.client("ecr").describe_repositories(repositoryNames=[PREFIX+"docs"])["repositories"][0]["repositoryUri"]
    s3=lab.client("s3");bucket=lab.outputs("storage")["BucketName"]
    s3.head_bucket(Bucket=bucket)
    observed["s3"]={"bucket":bucket,"versioning":s3.get_bucket_versioning(Bucket=bucket).get("Status")}
    observed["sqs"]=lab.client("sqs").get_queue_attributes(QueueUrl=lab.outputs("queues")["QueueUrl"],AttributeNames=["RedrivePolicy","VisibilityTimeout"])["Attributes"]
    function=lab.client("lambda").get_function_configuration(FunctionName=PREFIX+"diagnostic")
    observed["lambda"]={k:function.get(k) for k in ("State","Runtime","LoggingConfig","LastUpdateStatus")}
    logs=lab.client("logs").describe_log_streams(logGroupName="/aws/lambda/micro-test-local-diagnostic")["logStreams"]
    observed["lambdaLogStreams"]=len(logs)
    observed["stateMachineStatus"]=lab.client("stepfunctions").describe_state_machine(stateMachineArn=lab.outputs("workflow")["StateMachineArn"])["status"]
    pipe=owned_pipe(lab)
    observed["pipe"]={k:pipe.get(k) for k in ("CurrentState","DesiredState","Source","Target")} if pipe else "ABSENT"
    api=lab.outputs("api")["ApiId"]
    resources=lab.client("apigateway").get_resources(restApiId=api)["items"]
    resource=next(r for r in resources if r.get("path")=="/diagnostic")
    integration=lab.client("apigateway").get_integration(restApiId=api,resourceId=resource["id"],httpMethod="POST")
    observed["apiIntegration"]={k:integration[k] for k in ("type","httpMethod","uri")}
    save(ROOT/"reports/deployment.json",result)
    print(json.dumps(observed,indent=2,default=str))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--endpoint",required=True,type=endpoint_url)
    try:audit(Lab(p.parse_args().endpoint))
    except Exception as exc:
        print(f"FAIL: {exc}",file=sys.stderr);sys.exit(1)
