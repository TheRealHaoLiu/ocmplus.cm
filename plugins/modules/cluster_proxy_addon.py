from __future__ import (absolute_import, division, print_function)

__metaclass__ = type

from ansible.module_utils.basic import AnsibleModule
from jinja2 import Template
from kubernetes.dynamic.exceptions import NotFoundError
import kubernetes
import polling
import yaml

DOCUMENTATION = r'''

module: hub

short_description: 

author:
    - 

description:

options:
'''

EXAMPLES = r'''

'''

RETURN = r'''

'''

ADDON_TEMPLATE = Template("""
apiVersion: addon.open-cluster-management.io/v1alpha1
kind: ManagedClusterAddOn
metadata:
  name: {{ addon_name }}
  namespace: {{ managed_cluster_name }}
spec:
  installNamespace: open-cluster-management-agent-addon
""")

CLUSTER_PROXY_ADDON_TEMPLATE = Template("""
apiVersion: addon.open-cluster-management.io/v1alpha1
kind: ManagedClusterAddOn
metadata:
  name: cluster-proxy
  namespace: {{ managed_cluster_name }}
spec:
  installNamespace: open-cluster-management-agent-addon
""")

MANAGED_SERVICE_ACCOUNT_ADDON_TEMPLATE = Template("""
apiVersion: addon.open-cluster-management.io/v1alpha1
kind: ManagedClusterAddOn
metadata:
  name: managed-serviceaccount
  namespace: {{ managed_cluster_name }}
spec:
  installNamespace: open-cluster-management-agent-addon
""")

SERVICE_ACCOUNT_MANIFEST_WORK_TEMPLATE = Template("""
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: {{ service_account_name }}.serviceaccount
  namespace: {{ cluster_name }}
spec:
  workload:
    manifests:
    - apiVersion: v1
      kind: ServiceAccount
      metadata:
        name: {{ service_account_name }}
        namespace: {{ service_account_namespace }}
    - apiVersion: v1
      kind: Secret
      metadata:
        name: {{ service_account_name }}
        namespace: {{ service_account_namespace }}
        annotations:
          kubernetes.io/service-account.name: {{ service_account_name }}
      type: kubernetes.io/service-account-token
    - apiVersion: rbac.authorization.k8s.io/v1
      kind: ClusterRoleBinding
      metadata:
        name: {{ service_account_name }}
      roleRef:
        apiGroup: rbac.authorization.k8s.io
        kind: ClusterRole
        name: cluster-admin
      subjects:
        - kind: ServiceAccount
          name: {{ service_account_name }}
          namespace: {{ service_account_namespace }}
""")

MANAGED_SERVICE_ACCOUNT_TEMPLATE = Template("""
apiVersion: authentication.open-cluster-management.io/v1alpha1
kind: ManagedServiceAccount
metadata:
  name: cluster-proxy
  namespace: {{ cluster_name }}
spec:
  projected:
    type: None
  rotation: {}
""")

MANAGED_SERVICE_ACCOUNT_CLUSTER_ROLE_BINDING_TEMPLATE = Template("""
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: {{ managed_service_account_name }}.cluster-role-binding
  namespace: {{ cluster_name }}
spec:
  workload:
    manifests:
    - apiVersion: rbac.authorization.k8s.io/v1
      kind: ClusterRoleBinding
      metadata:
        name: {{ managed_service_account_name }}
      roleRef:
        apiGroup: rbac.authorization.k8s.io
        kind: ClusterRole
        name: cluster-admin
      subjects:
        - kind: ServiceAccount
          name: {{ managed_service_account_name }}
          namespace: {{ managed_service_account_namespace }}
""")


def ensure_cluster_proxy_feature_enabled(hub_client: kubernetes.dynamic.DynamicClient) -> dict:
    # get all instance of mch
    mch_api = hub_client.resources.get(
        api_version="operator.open-cluster-management.io/v1",
        kind="MultiClusterHub",
    )

    mch_list = mch_api.get()
    if len(mch_list.get('items', [])) != 1:
        # TODO: throw error
        return mch_list

    mch = mch_list.items[0]
    if mch.spec.enableClusterProxyAddon:
        return mch

    mch.spec.enableClusterProxyAddon = True
    mch = mch_api.patch(
        name=mch.metadata.name,
        namespace=mch.metadata.namespace,
        body=mch,
        content_type="application/merge-patch+json",
    )

    return mch


def ensure_cluster_proxy_addon_enable(hub_client: kubernetes.dynamic.DynamicClient, managed_cluster_name: str):
    managed_cluster_addon_api = hub_client.resources.get(
        api_version="addon.open-cluster-management.io/v1alpha1",
        kind="ManagedClusterAddOn",
    )

    try:
        cluster_proxy_addon = managed_cluster_addon_api.get(
            name='cluster-proxy',
            namespace=managed_cluster_name,
        )
    except NotFoundError:
        new_cluster_proxy_addon_raw = CLUSTER_PROXY_ADDON_TEMPLATE.render(
            managed_cluster_name=managed_cluster_name,
        )
        new_cluster_proxy_addon = yaml.safe_load(new_cluster_proxy_addon_raw)
        cluster_proxy_addon = managed_cluster_addon_api.create(new_cluster_proxy_addon)

    return cluster_proxy_addon


def ensure_managed_service_account_feature_enabled(hub_client: kubernetes.dynamic.DynamicClient):
    # NOTE: managed service account is not a supported feature in ACM yet and it's currently a upstream proposed feature
    #       for more information see https://github.com/open-cluster-management-io/enhancements/pull/24
    # TODO: the code currently only check if managed-serviceaccount feature is enabled
    #  it does not enable the feature yet this code will need to be updated when the feature become officially part of
    #  ACM

    cluster_management_addon_api = hub_client.resources.get(
        api_version="addon.open-cluster-management.io/v1alpha1",
        kind="ClusterManagementAddOn",
    )

    return cluster_management_addon_api.get(name='managed-serviceaccount')


def ensure_managed_service_account_addon_enabled(
        hub_client: kubernetes.dynamic.DynamicClient,
        managed_cluster_name: str
):
    managed_cluster_addon_api = hub_client.resources.get(
        api_version="addon.open-cluster-management.io/v1alpha1",
        kind="ManagedClusterAddOn",
    )

    try:
        managed_service_account_addon = managed_cluster_addon_api.get(
            name='managed-serviceaccount',
            namespace=managed_cluster_name,
        )
    except NotFoundError:
        new_managed_service_account_addon_raw = MANAGED_SERVICE_ACCOUNT_ADDON_TEMPLATE.render(
            managed_cluster_name=managed_cluster_name,
        )
        managed_service_account_addon_yaml = yaml.safe_load(new_managed_service_account_addon_raw)
        managed_service_account_addon = managed_cluster_addon_api.create(managed_service_account_addon_yaml)

    return managed_service_account_addon


def ensure_remote_service_account(
        hub_client: kubernetes.dynamic.DynamicClient,
        cluster_name: str,
        name: str,
        namespace: str
):
    manifest_work_api = hub_client.resources.get(
        api_version="work.open-cluster-management.io/v1",
        kind="ManifestWork",
    )

    new_service_account_manifest_work_raw = SERVICE_ACCOUNT_MANIFEST_WORK_TEMPLATE.render(
        cluster_name=cluster_name,
        service_account_name=name,
        service_account_namespace=namespace,
    )

    new_service_account_manifest_work = yaml.safe_load(new_service_account_manifest_work_raw)

    try:
        service_account_manifest_work = manifest_work_api.get(
            name=new_service_account_manifest_work['metadata']['name'],
            namespace=new_service_account_manifest_work['metadata']['namespace'],
        )
        # TODO: validate existing service_account_manifest_work and verify that it is what we expected update if not
    except NotFoundError:
        service_account_manifest_work = manifest_work_api.create(new_service_account_manifest_work)
        # TODO: we may need to wait for the manifest work to be applied

    return service_account_manifest_work


def ensure_addon_enabled(
        hub_client: kubernetes.dynamic.DynamicClient,
        addon_name: str,
        managed_cluster_name: str,
):
    managed_cluster_addon_api = hub_client.resources.get(
        api_version="addon.open-cluster-management.io/v1alpha1",
        kind="ManagedClusterAddOn",
    )

    try:
        addon = managed_cluster_addon_api.get(
            name=addon_name,
            namespace=managed_cluster_name,
        )
    except NotFoundError:
        new_addon_raw = ADDON_TEMPLATE.render(
            addon_name=addon_name,
            managed_cluster_name=managed_cluster_name,
        )
        addon_yaml = yaml.safe_load(new_addon_raw)
        addon = managed_cluster_addon_api.create(addon_yaml)

    return addon


def wait_for_addon_available(hub_client: kubernetes.dynamic.DynamicClient, addon):
    managed_cluster_addon_api = hub_client.resources.get(
        api_version="addon.open-cluster-management.io/v1alpha1",
        kind="ManagedClusterAddOn",
    )

    def check_response(response):
        for condition in response['status']['conditions']:
            if condition['type'] == 'Available':
                return condition['status']
        return False

    addon = polling.poll(
        target=lambda: managed_cluster_addon_api.get(
            name=addon.metadata.name,
            namespace=addon.metadata.namespace,
        ),
        check_success=check_response,
        step=0.1,
        timeout=60,
    )

    return addon


def get_managed_cluster(hub_client: kubernetes.dynamic.DynamicClient, managed_cluster_name: str):
    managed_cluster_api = hub_client.resources.get(
        api_version="cluster.open-cluster-management.io/v1",
        kind="ManagedCluster",
    )

    try:
        managed_cluster = managed_cluster_api.get(name=managed_cluster_name)
    except NotFoundError:
        return None

    return managed_cluster


def get_current_user(hub_client: kubernetes.dynamic.DynamicClient) -> str:
    user_api = hub_client.resources.get(
        api_version="user.openshift.io/v1",
        kind="User",
    )

    return user_api.get(name='~')


def ensure_managed_service_account(hub_client: kubernetes.dynamic.DynamicClient, managed_service_account_addon):
    managed_cluster_name = managed_service_account_addon.metadata.namespace
    managed_cluster_namespace = managed_service_account_addon.metadata.namespace

    managed_service_account_api = hub_client.resources.get(
        api_version="authentication.open-cluster-management.io/v1alpha1",
        kind="ManagedServiceAccount",
    )

    try:
        managed_service_account = managed_service_account_api.get(
            name='cluster-proxy',
            namespace=managed_cluster_namespace,
        )
    except NotFoundError:
        new_managed_service_account_raw = MANAGED_SERVICE_ACCOUNT_TEMPLATE.render(
            cluster_name=managed_cluster_name,
        )
        managed_service_account_yaml = yaml.safe_load(new_managed_service_account_raw)
        managed_service_account = managed_service_account_api.create(managed_service_account_yaml)

    return managed_service_account


def ensure_managed_service_account_rbac(hub_client, managed_service_account, managed_service_account_addon):
    managed_cluster_name = managed_service_account_addon.metadata.namespace
    managed_service_account_name = managed_service_account.metadata.name
    managed_service_account_namespace = managed_service_account_addon.spec.installNamespace

    manifest_work_api = hub_client.resources.get(
        api_version="work.open-cluster-management.io/v1",
        kind="ManifestWork",
    )

    new_manifest_work_raw = MANAGED_SERVICE_ACCOUNT_CLUSTER_ROLE_BINDING_TEMPLATE.render(
        cluster_name=managed_cluster_name,
        managed_service_account_name=managed_service_account_name,
        managed_service_account_namespace=managed_service_account_namespace,
    )

    new_manifest_work = yaml.safe_load(new_manifest_work_raw)

    try:
        manifest_work = manifest_work_api.get(
            name=new_manifest_work['metadata']['name'],
            namespace=new_manifest_work['metadata']['namespace'],
        )
        # TODO: validate existing service_account_manifest_work and verify that it is what we expected update if not
    except NotFoundError:
        manifest_work = manifest_work_api.create(new_manifest_work)
        # TODO: we may need to wait for the manifest work to be applied

    return manifest_work


def execute_module(module):
    managed_cluster_name = module.params['managed_cluster']

    hub_kubeconfig = kubernetes.config.load_kube_config(config_file=module.params['hub_kubeconfig'])
    hub_client = kubernetes.dynamic.DynamicClient(
        kubernetes.client.api_client.ApiClient(configuration=hub_kubeconfig)
    )

    # TODO: RBAC validation?
    # - must have access to managedcluster
    # - must be able to create manifestwork
    # - must be able to create managedclusterview
    # ...

    managed_cluster = get_managed_cluster(hub_client, managed_cluster_name)
    if managed_cluster is None:
        # TODO: throw error and exit
        module.exit_json()
        # TODO: there might be other exit condition

    ensure_cluster_proxy_feature_enabled(hub_client)
    cluster_proxy_addon = ensure_addon_enabled(hub_client, "cluster-proxy", managed_cluster_name)

    ensure_managed_service_account_feature_enabled(hub_client)
    managed_service_account_addon = ensure_addon_enabled(hub_client, "managed-serviceaccount", managed_cluster_name)

    wait_for_addon_available(hub_client, cluster_proxy_addon)
    managed_service_account_addon = wait_for_addon_available(hub_client, managed_service_account_addon)

    managed_service_account = ensure_managed_service_account(hub_client, managed_service_account_addon)
    ensure_managed_service_account_rbac(hub_client, managed_service_account, managed_service_account_addon)

    # TODO: generate kubeconfig
    # TODO: figure out where to get CA data


def main():
    argument_spec = dict(
        hub_kubeconfig=dict(type='str', required=True),
        managed_cluster=dict(type='str', required=True),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    execute_module(module)


if __name__ == '__main__':
    main()
