type LosslessTransport={usesMethod(method:string):boolean;stringify(value:unknown):string;parse(value:string):unknown};
/** Reuse the App's lossless document wire, outside generic DSH RPC/streams.
 * Tests without the desktop document facade may supply direct typed mocks.
 */
export async function callAppDocumentApi(host:object,method:string,args:unknown[]):Promise<unknown>{
  const api=host as Record<string,unknown>;
  const transport=(window as unknown as {JSONTransport?:LosslessTransport}).JSONTransport;
  if(transport){
    if(typeof api.document_json_call!=="function")throw new Error("dsh_document_transport_unavailable");
    if(!transport.usesMethod(method))throw new Error(`dsh_document_transport_method_unregistered:${method}`);
    const response=await Reflect.apply(api.document_json_call,host,[method,transport.stringify(args)]);
    if(typeof response!=="string")throw new Error("dsh_document_transport_response_invalid");
    return transport.parse(response);
  }
  const target=api[method];if(typeof target!=="function")throw new Error(`dsh_document_api_unavailable:${method}`);
  return Reflect.apply(target,host,args);
}
