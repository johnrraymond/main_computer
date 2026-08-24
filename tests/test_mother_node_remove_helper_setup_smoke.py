import yaml

from tools.mother.common import mother_node_remove_helper_setup_smoke as smoke


def test_runner_compose_preserves_shell_variables_for_runner_container():
    runner_script = smoke._runner_script(
        node="mainneta-super1",
        service_uuid="hndpvx15ciqpqpxptnrslibt",
        helper_name="mother-node-remove-helper-setup-mainneta-super1-hndpvx15ciqpqpxptnrslibt",
        proof_host_port=39403,
        controller_id="coolify-a",
    )

    assert 'BESU_NAME="${NODE}-${SERVICE_UUID}"' in runner_script
    assert 'name=^/${BESU_NAME}$' in runner_script
    assert '$BESU_ID' in runner_script

    compose_text = smoke._runner_compose("mother-node-remove-helper-setup-runner", runner_script)
    command = yaml.safe_load(compose_text)["services"]["mother-node-remove-helper-setup-runner"]["command"][2]

    assert 'BESU_NAME="$${NODE}-$${SERVICE_UUID}"' in command
    assert 'name=^/$${BESU_NAME}$$' in command
    assert '$$BESU_ID' in command
    assert 'MOTHER_RPC_URL="http://$${NODE}:8545"' in command
