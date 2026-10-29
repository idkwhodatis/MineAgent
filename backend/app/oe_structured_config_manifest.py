"""Host-only exact installed Config projection; never import OpenEvolve here.

Captured from the verified retained phase8 OpenEvolve0.4.0 installation through
normal provider_runtime_config.build_config/config_manifest. Only the declared
mutation bound and public model name/token limit vary; all default fields stay
frozen. Differential installed tests are required when the runtime changes.
No endpoint/key or caller-provided Config dictionary is accepted.
"""

import hashlib
import json

from packages.openevolve_structured.candidate import _raw_fields, project_candidate
from packages.openevolve_structured.contracts import StructuredSearchSpec
from packages.openevolve_structured.provider_contracts import OEChatBinding

_CAPTURED_SHA256 = "0d3581eba3f3571ea66136e66f6b15ba60bbe3a24e3dd1cbe455690f409c7c8c"
_CAPTURED = b'{"checkpoint_interval":1,"convergence_threshold":0.001,"database":{"archive_size":8,"artifact_retention_days":30,"artifact_size_threshold":32768,"artifacts_base_path":null,"cleanup_old_artifacts":true,"db_path":null,"diversity_metric":"edit_distance","diversity_reference_size":20,"elite_selection_ratio":0.1,"embedding_api_base":null,"embedding_model":null,"exploitation_ratio":0.7,"exploration_ratio":0.2,"feature_bins":10,"feature_dimensions":["complexity","diversity"],"in_memory":true,"log_prompts":false,"max_snapshot_artifacts":8,"migration_interval":50,"migration_rate":0.1,"novelty_llm":null,"num_islands":1,"population_size":8,"random_seed":0,"similarity_threshold":0.99},"diff_based_evolution":false,"diff_pattern":"<<<<<<< SEARCH\\\\n(.*?)=======\\\\n(.*?)>>>>>>> REPLACE","early_stopping_metric":"combined_score","early_stopping_patience":null,"enforce_evolve_blocks":false,"evaluator":{"cascade_evaluation":false,"cascade_thresholds":[0.5,0.75,0.9],"cpu_limit":null,"distributed":false,"enable_artifacts":true,"evaluator_args":null,"llm_feedback_weight":0.1,"max_artifact_storage":16384,"max_retries":0,"memory_limit_mb":null,"parallel_evaluations":1,"timeout":90,"use_llm_feedback":false},"evolution_trace":{"buffer_size":10,"compress":false,"enabled":false,"format":"jsonl","include_code":false,"include_prompts":false,"output_path":null},"file_suffix":".json","language":"json","llm":{"_manual_queue_dir":null,"allow_all_tools":null,"api_base":null,"api_key":null,"evaluator_models":[{"_manual_queue_dir":null,"allow_all_tools":null,"api_base":null,"api_key":null,"init_client":{"installed_factory":"packages.openevolve_structured.provider_broker_client.init_client"},"manual_mode":false,"max_ai_credits":null,"max_budget_usd":null,"max_tokens":4096,"name":"chosen-model","provider":null,"random_seed":0,"reasoning_effort":null,"retries":0,"retry_delay":0,"system_message":null,"temperature":0,"timeout":65,"top_p":null,"weight":1.0}],"init_client":null,"manual_mode":false,"max_ai_credits":null,"max_budget_usd":null,"max_tokens":4096,"models":[{"_manual_queue_dir":null,"allow_all_tools":null,"api_base":null,"api_key":null,"init_client":{"installed_factory":"packages.openevolve_structured.provider_broker_client.init_client"},"manual_mode":false,"max_ai_credits":null,"max_budget_usd":null,"max_tokens":4096,"name":"chosen-model","provider":null,"random_seed":0,"reasoning_effort":null,"retries":0,"retry_delay":0,"system_message":null,"temperature":0,"timeout":65,"top_p":null,"weight":1.0}],"name":null,"primary_model":null,"primary_model_weight":null,"provider":null,"random_seed":null,"reasoning_effort":null,"retries":0,"retry_delay":0,"secondary_model":null,"secondary_model_weight":null,"system_message":"system_message","temperature":0,"timeout":65,"top_p":null,"weight":1.0},"log_dir":null,"log_level":"INFO","logger_prefix":null,"max_code_length":32768,"max_iterations":1,"max_tasks_per_child":null,"prompt":{"artifact_security_filter":true,"code_length_threshold":null,"comprehensive_implementation_min_lines":50,"concise_implementation_max_lines":10,"diff_summary_max_line_len":100,"diff_summary_max_lines":30,"evaluator_system_message":"evaluator_system_message","include_artifacts":true,"include_changes_under_chars":100,"initial_changes_description":"","island_system_messages":[],"max_artifact_bytes":8192,"meta_prompt_weight":0.1,"num_diverse_programs":1,"num_top_programs":1,"programs_as_changes_description":false,"suggest_simplification_after_chars":500,"system_message":"Return one complete canonical JSON structured candidate, not Python, not a patch, and not markdown. Use sorted keys, compact separators and finite numbers. Preserve every current program field except threshold, top_k, maximum_weight and maximum_gross_weight. Only these four bounded parameters may change. The completed factor snapshot, market, execution window and objective are fixed. Do not invent factor values, labels, fills or evaluation metrics.","system_message_changes_description":null,"template_dir":null,"template_variations":{},"use_meta_prompting":false,"use_template_stochasticity":false},"random_seed":0}'


def expected_config_manifest(spec, seed, binding):
    if type(spec) is not StructuredSearchSpec or type(binding) is not OEChatBinding:
        raise ValueError("Exact structured search and public model binding required")
    spec = StructuredSearchSpec.model_validate(_raw_fields(spec))
    binding = OEChatBinding.model_validate(_raw_fields(binding))
    candidate = project_candidate(seed, spec.domain)
    if candidate.candidate_sha256 != spec.seed_candidate_sha256:
        raise ValueError("Actual declared seed differs")
    if hashlib.sha256(_CAPTURED).hexdigest() != _CAPTURED_SHA256:
        raise ValueError("Pinned installed Config template differs")
    value = json.loads(_CAPTURED)
    value["max_iterations"] = spec.maximum_mutation_slots
    value["llm"]["max_tokens"] = binding.max_output_tokens
    for model in (*value["llm"]["models"], *value["llm"]["evaluator_models"]):
        model["name"] = binding.model
        model["max_tokens"] = binding.max_output_tokens
    return value
