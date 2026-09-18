/** Offline inspection only. Never creates state, takes locks or runs capabilities. */
import { accessSync, constants, statSync } from 'node:fs';
import { prepareConfig } from '../integration/run.mjs';
import { statusConfig } from './coordinator.mjs';
export function checkConfig(path) {
  const result={schema:'openprose.weave-check/1',status:'blocked',providerVerified:false,semanticAssessment:false,
    runtime:{name:typeof Bun==='undefined'?'unsupported':'bun',version:typeof Bun==='undefined'?null:Bun.version},
    configuration:'unchecked',sources:{status:'unchecked',selectedCount:null},executables:{assessor:'unchecked',actor:'unchecked'},
    environment:{selected:[],missing:[]},checkpoint:{status:'unchecked',serviceOwned:null,locked:null},errors:[]};
  if(typeof Bun==='undefined') result.errors.push('BUN_REQUIRED');
  let selected;
  try {selected=prepareConfig(path,{allowMissingEnvironment:true});result.configuration='valid';}
  catch {result.configuration='invalid';result.errors.push('CONFIGURATION_INVALID_OR_UNAVAILABLE');return result;}
  const {config,bound,missingEnvironment}=selected;
  result.environment={selected:[...(config.environmentKeys??[])],missing:[...missingEnvironment]};
  if(missingEnvironment.length) result.errors.push('ENVIRONMENT_MISSING');
  for(const role of ['assessor','actor']) {
    try {if(!statSync(config[role][0]).isFile())throw Error();accessSync(config[role][0],constants.X_OK);result.executables[role]='available';}
    catch {result.executables[role]='unavailable';result.errors.push(role==='assessor'?'ASSESSOR_EXECUTABLE_UNAVAILABLE':'ACTOR_EXECUTABLE_UNAVAILABLE');}
  }
  result.sources.selectedCount=1+config.contracts.length+config.evidence.length;
  try {result.sources.status=bound.observe(Date.now()).gap?'gap':'available';}
  catch {result.sources.status='gap';}
  if(result.sources.status==='gap') result.errors.push('SELECTED_SOURCE_GAP');
  try {
    const status=statusConfig(path);
    result.checkpoint={status:status.checkpoint===null?'absent':status.checkpoint.pending!==null?'pending':'present',serviceOwned:status.serviceOwned,locked:status.checkpointLocked};
    if(status.checkpoint?.pending!==null && status.checkpoint!==null) result.errors.push('RECOVERY_REQUIRED');
    if(status.serviceOwned || status.checkpointLocked) result.errors.push('EXISTING_OWNER_OR_HOST_LOCK');
  } catch {result.checkpoint.status='invalid-or-unreadable';result.errors.push('CHECKPOINT_INVALID_OR_UNREADABLE');}
  result.status=result.errors.length?'blocked':'configured';
  return result;
}
