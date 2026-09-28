"""CPU-only tests using the installed ChainedOptimizer property source."""
import ast
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

STAGE = Path(__file__).resolve().parent
LIVE = STAGE / 'runtime_observe.before.py'
ORIGINAL = LIVE.read_text()
TREE = ast.parse(ORIGINAL)
NODES = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in ('_bounded_scalar', '_optimizer_checkpoint_scalars')]
PROPERTY = json.loads((STAGE/'installed-property.json').read_text())
namespace = {'math': math}
exec(compile(ast.fix_missing_locations(ast.Module(body=NODES, type_ignores=[])), str(LIVE), 'exec'), namespace)
OLD = namespace['_optimizer_checkpoint_scalars']; BOUNDED = namespace['_bounded_scalar']
NEW_SOURCE = (STAGE/'scalar_observer_v2.py').read_text()
new_namespace = {'_bounded_scalar': BOUNDED}
exec(compile(NEW_SOURCE, '<candidate>', 'exec'), new_namespace)
NEW = new_namespace['_optimizer_checkpoint_scalars']
prop = ast.parse(PROPERTY['property_source']).body[0]
chain_class = ast.ClassDef(name='InstalledPropertyChain', bases=[], keywords=[], body=[prop], decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[chain_class], type_ignores=[])), '<installed-property>', 'exec'), namespace)
CHAIN = namespace['InstalledPropertyChain']
spec=importlib.util.spec_from_file_location('patch_driver', STAGE/'worker_scalar_patch.py')
DRIVER=importlib.util.module_from_spec(spec);spec.loader.exec_module(DRIVER)


def leaf(step=1):
    parameter=object()
    return SimpleNamespace(param_groups=[{'params':[parameter],'step':step,'lr':1e-6,'betas':(.9,.999),'eps':1e-8,'weight_decay':.1},
                                        {'params':[],'lr':1e-6,'betas':(.9,.999),'eps':1e-8,'weight_decay':.1}], state={parameter:{}})


def chain():
    value=CHAIN();value.chained_optimizers=[SimpleNamespace(optimizer=leaf()),SimpleNamespace(optimizer=leaf())]
    value.param_groups=[];value.state={}
    return value


class ScalarTests(unittest.TestCase):
    def test_original_bug_and_installed_two_child_property(self):
        value=chain()
        with self.assertRaisesRegex(AssertionError,'more than one optimizer'):OLD(value)
        result=NEW(value)
        self.assertEqual(len(result['step_counters']),2)
        self.assertEqual(set(result['step_counters'].values()),{1})

    def test_empty_te_groups_normalized_on_load_do_not_false_mismatch(self):
        value=chain();before=NEW(value)
        for wrapped in value.chained_optimizers:wrapped.optimizer.param_groups[1]['step']=1
        self.assertEqual(NEW(value),before)
        self.assertFalse(any('.group1.step' in key for key in before['step_counters']))

    def test_nonempty_step_difference_is_visible(self):
        value=chain();before=NEW(value);value.chained_optimizers[1].optimizer.param_groups[0]['step']=2
        self.assertNotEqual(NEW(value)['step_counters'],before['step_counters'])

    def test_native_torch_style_per_parameter_counter_fallback(self):
        parameter=object();value=SimpleNamespace(param_groups=[{'params':[parameter],'lr':1e-6}],state={parameter:{'step':3}})
        self.assertEqual(list(NEW(value)['step_counters'].values()),[3])

    def test_optimizer_properties_are_never_invoked(self):
        class Unsupported:
            @property
            def optimizer(self):raise AssertionError('must not invoke')
            @property
            def state(self):raise AssertionError('must not invoke')
            @property
            def param_groups(self):raise AssertionError('must not invoke')
        self.assertTrue(NEW(Unsupported())['unavailable'])

    def test_counter_budget_is_bounded_and_inspection_does_not_modify_data(self):
        parameters=[object() for _ in range(100)]
        value=SimpleNamespace(param_groups=[{'params':parameters,'lr':1e-6}],state={p:{'step':1} for p in parameters})
        state_ids={id(k):id(v) for k,v in value.state.items()};groups_id=id(value.param_groups)
        self.assertLessEqual(len(NEW(value)['step_counters']),32)
        self.assertEqual(state_ids,{id(k):id(v) for k,v in value.state.items()});self.assertEqual(groups_id,id(value.param_groups))

    def test_module_patch_changes_exactly_one_binding_and_later_global_lookup(self):
        with tempfile.TemporaryDirectory(prefix='scalar-patch-test-') as directory:
            path=Path(directory)/'runtime_observe.py';path.write_text(ORIGINAL)
            module=ModuleType('runtime_observe');module.__file__=str(path);module.math=math
            exec(compile(ast.fix_missing_locations(ast.Module(body=NODES,type_ignores=[])),str(path),'exec'),vars(module))
            exec('def checkpoint_lookup(value):\n return _optimizer_checkpoint_scalars(value)\n',vars(module))
            sentinel=object();module.unchanged_sentinel=sentinel
            expected={'actor_id':'actor','namespace':DRIVER.NAMESPACE,'job_id':'job','node_id':'node','pid':123,
                      'pid_start_ticks':'tick','rank':0,'world_size':8,'run_id':DRIVER.NAMESPACE,'attempt_id':DRIVER.ATTEMPT,
                      'experiment_root':'/experiment','is_actor':True,'role':'actor_rollout',
                      'engine_class':'MegatronEngineWithLMHead','optimizer_class':'ChainedOptimizer','optimizer_children':2,
                      'observed_optimizer_calls':0,'active_training_global_step':None,'observer_file_sha256':hashlib.sha256(ORIGINAL.encode()).hexdigest()}
            with patch.dict(sys.modules,{'runtime_observe':module}),patch.object(DRIVER,'inspect_worker',return_value=expected):
                result=DRIVER.apply_one(object(),expected,NEW_SOURCE,hashlib.sha256(NEW_SOURCE.encode()).hexdigest())
                again=DRIVER.apply_one(object(),expected,NEW_SOURCE,hashlib.sha256(NEW_SOURCE.encode()).hexdigest())
            self.assertTrue(result['applied']);self.assertTrue(again['already_applied'])
            self.assertEqual(result['other_module_bindings_changed'],[]);self.assertIs(module.unchanged_sentinel,sentinel)
            self.assertEqual(len(module.checkpoint_lookup(chain())['step_counters']),2)
            self.assertEqual(path.read_text(),ORIGINAL)

    def test_started_update_refuses_any_patch(self):
        current={'observed_optimizer_calls':1,'active_training_global_step':1}
        keys=('actor_id','namespace','job_id','node_id','pid','pid_start_ticks','rank','world_size','run_id','attempt_id','experiment_root','is_actor','role','engine_class','optimizer_class','optimizer_children')
        current.update({key:'same' for key in keys})
        with patch.object(DRIVER,'inspect_worker',return_value=current):
            with self.assertRaisesRegex(RuntimeError,'begun a training update'):
                DRIVER.apply_one(object(),current,NEW_SOURCE,hashlib.sha256(NEW_SOURCE.encode()).hexdigest())


if __name__=='__main__':unittest.main()
