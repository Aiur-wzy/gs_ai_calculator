/** Typed failures on App's bare binding seams; DSH Session state is untouched. */
export class AppDomainHostError extends Error {
  constructor(readonly code:string,message:string,readonly details:unknown,readonly operationMayHaveCompleted:boolean){super(message||"App 暂时无法处理当前文档，请查看操作详情后继续。");this.name="AppDomainHostError";}
}
export function assertAppDomainSuccess(value:unknown):void{
  if(value&&typeof value==="object"&&!Array.isArray(value)){
    const row=value as Record<string,unknown>;
    if(row.code===1)throw new AppDomainHostError(typeof row.reason==="string"?row.reason:"app_domain_failed",typeof row.message==="string"?row.message:"",row.details,row.operation_may_have_completed===true);
  }
}

const DOMAIN_UNAVAILABLE=new Set(["native_app_binding_database_changed","agent_database_changed","database_missing","database_not_installed","dsh_document_not_bound","dsh_domain_session_unbound","native_app_binding_compatibility_missing","native_app_binding_compatibility_changed"]);
const COMPATIBILITY_CODES=new Set(["native_app_binding_compatibility_missing","native_app_binding_compatibility_changed"]);
/** Known domain absence only. Unknown outcomes must retain their recovery path. */
export function canContinueWithoutAppDomain(error:unknown):boolean{
  return error instanceof AppDomainHostError&&!error.operationMayHaveCompleted&&DOMAIN_UNAVAILABLE.has(error.code);
}
export function isAppDomainIncompatibility(error:unknown):error is AppDomainHostError{
  return error instanceof AppDomainHostError&&COMPATIBILITY_CODES.has(error.code);
}
const COMPONENT_LABELS={
  native_shell_release:"Agent 运行组件",
  release_pair_identity_hash:"客户端与资料库发行配对",
  app_artifact_identity_hash:"客户端与计算规则",
  skill_bundle_hash:"拉表说明",
} as const;
export type AppDomainIncompatibility=Readonly<{
  state:"missing"|"changed";
  differences:readonly Readonly<{component:string;label:string;recorded:unknown;current:unknown}>[];
  migrationPrompt?:string;
  detailsValid:boolean;
}>;
/** Presentation only: these diagnostics never grant a candidate permission. */
export function appDomainIncompatibility(error:unknown):AppDomainIncompatibility|undefined{
  if(!isAppDomainIncompatibility(error))return undefined;
  const state=error.code==="native_app_binding_compatibility_missing"?"missing":"changed";
  const details=error.details&&typeof error.details==="object"&&!Array.isArray(error.details)?error.details as Record<string,unknown>:undefined;
  const valid=details?.contract==="genshin-native-domain-incompatibility-v1"&&details.state===state&&Array.isArray(details.differences);
  const differences:AppDomainIncompatibility["differences"][number][]=[];
  let detailsValid=valid;
  if(valid)for(const value of details.differences as unknown[]){
    if(!value||typeof value!=="object"||Array.isArray(value)){detailsValid=false;continue;}
    const row=value as Record<string,unknown>;
    if(typeof row.component!=="string"||!Object.hasOwn(COMPONENT_LABELS,row.component)||!Object.hasOwn(row,"recorded")||!Object.hasOwn(row,"current")){detailsValid=false;continue;}
    differences.push({component:row.component,label:COMPONENT_LABELS[row.component as keyof typeof COMPONENT_LABELS],recorded:row.recorded,current:row.current});
  }
  const migrationPrompt=detailsValid&&typeof details?.migration_prompt==="string"&&details.migration_prompt.trim()?details.migration_prompt:undefined;
  return {state,differences,detailsValid,...(migrationPrompt?{migrationPrompt}:{})};
}
