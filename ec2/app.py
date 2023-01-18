#!/usr/bin/env python3

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2,
    aws_ecs,
    aws_ecs_patterns,
    aws_servicediscovery,
    aws_iam,
)

from os import getenv
from aws_cdk import App, Stack
from constructs import Construct

# Creating a construct that will populate the required objects created in the platform repo such as vpc, ecs cluster, and service discovery namespace
class BasePlatform(Construct):

    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.environment_name = 'ecsworkshop'

        # The base platform stack is where the VPC was created, so all we need is the name to do a lookup and import it into this stack for use
        self.vpc = aws_ec2.Vpc.from_lookup(
            self, "VPC",
            vpc_name='{}-base/BaseVPC'.format(self.environment_name)
        )

        self.sd_namespace = aws_servicediscovery.PrivateDnsNamespace.from_private_dns_namespace_attributes(
            self, "SDNamespace",
            namespace_name=cdk.Fn.import_value('NSNAME'),
            namespace_arn=cdk.Fn.import_value('NSARN'),
            namespace_id=cdk.Fn.import_value('NSID')
        )

        # If using EC2 backed, this will take all security groups assigned to the cluster nodes and create a list
        # This list will be used when importing the cluster
        cluster_output_sec_grp = cdk.Fn.import_value('ECSSecGrpList')

        self.ecs_cluster = aws_ecs.Cluster.from_cluster_attributes(
            self, "ECSCluster",
            cluster_name=cdk.Fn.import_value('ECSClusterName'),
            security_groups=[aws_ec2.SecurityGroup.from_security_group_id(self, "ClusterSecGrp", cluster_output_sec_grp)],
            vpc=self.vpc,
            default_cloud_map_namespace=self.sd_namespace
        )


class CapacityProviderEC2Service(Stack):

    def __init__(self, scope: Stack, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        self.base_platform = BasePlatform(self, self.stack_name)

        self.task_image = aws_ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
            image=aws_ecs.ContainerImage.from_registry("adam9098/ecsdemo-capacityproviders:latest"),
            container_port=5000,
            environment={
                'AWS_DEFAULT_REGION': getenv('AWS_DEFAULT_REGION')
            }
        )

        self.load_balanced_service = aws_ecs_patterns.ApplicationLoadBalancedEc2Service(
            self, "EC2CapacityProviderService",
            service_name='ecsdemo-capacityproviders-ec2',
            cluster=self.base_platform.ecs_cluster,
            cpu=256,
            memory_limit_mib=512,
            desired_count=1,
            #desired_count=10,
            public_load_balancer=True,
            task_image_options=self.task_image,
        )

        # Update Target group settings for Spot instances to adjust deregistration delay to less than 120 sec.
        # Adjust healthy threshold  to 2 to reduce the time for a new task to be healthy in 1 minute

        self.cfn_target_group = self.load_balanced_service.node.find_child('LB'
                                ).node.find_child('PublicListener'
                                ).node.find_child('ECSGroup'
                                ).node.default_child
        self.cfn_target_group.target_group_attributes = [{ "key" : "deregistration_delay.timeout_seconds", "value": "90" }]
        self.cfn_target_group.healthy_threshold_count = 2

        # This should work, but the default child is not the service cfn, it's a list of cfn service and sec group
        #self.cfn_resource = self.load_balanced_service.service.node.default_child
        self.cfn_resource = self.load_balanced_service.service.node.children[0]

        self.cfn_resource.add_deletion_override("Properties.LaunchType")

        self.load_balanced_service.task_definition.add_to_task_role_policy(
            aws_iam.PolicyStatement(
                actions=[
                    'ecs:ListTasks',
                    'ecs:DescribeTasks'
                ],
                resources=['*']
            )
        )


_env = cdk.Environment(account=getenv('AWS_ACCOUNT_ID'), region=getenv('AWS_DEFAULT_REGION'))
environment = "ecsworkshop"
stack_name = "{}-capacityproviders-ec2".format(environment)
app = cdk.App()
CapacityProviderEC2Service(app, stack_name, env=_env)
app.synth()
