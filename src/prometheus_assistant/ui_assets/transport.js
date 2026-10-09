(function(root){
'use strict';
class LocalClient{
 constructor(fetcher=root.fetch){this.fetcher=fetcher.bind(root);this.token='';this.bootstrapPromise=null;this.onRestart=null;}
 async bootstrap(){
  if(this.bootstrapPromise)return this.bootstrapPromise;
  this.bootstrapPromise=(async()=>{const response=await this.fetcher('/api/bootstrap',{cache:'no-store',signal:AbortSignal.timeout(12000)});const data=await response.json();if(!response.ok||!data.token)throw new Error('Unable to reconnect to Prometheus.');const changed=this.token&&this.token!==data.token;this.token=data.token;if(changed&&this.onRestart)this.onRestart();return data;})();
  try{return await this.bootstrapPromise;}finally{this.bootstrapPromise=null;}
 }
 async request(path,data,retry=true){
  if(path==='/api/bootstrap')return this.bootstrap();
  if(!this.token)await this.bootstrap();
  const headers={'X-Prometheus-Token':this.token};const options={headers,cache:'no-store',signal:AbortSignal.timeout(12000)};
  if(data!==undefined){headers['Content-Type']='application/json';options.method='POST';options.body=JSON.stringify(data);}
  const response=await this.fetcher(path,options);const result=await response.json();
  if(response.status===403&&retry){await this.bootstrap();return this.request(path,data,false);}
  if(!response.ok){const error=new Error(result.error||'The local request did not complete.');error.status=response.status;throw error;}
  return result;
 }
}
root.PrometheusTransport={LocalClient};if(typeof module!=='undefined')module.exports=root.PrometheusTransport;
})(typeof globalThis!=='undefined'?globalThis:this);
