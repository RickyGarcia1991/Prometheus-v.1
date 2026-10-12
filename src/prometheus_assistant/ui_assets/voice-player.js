/* Local PCM playback and a pulse measured from that same audible stream. */
(function(root){
'use strict';
function speechText(text){
 // This is a speech-only rendering; the complete answer stays in the conversation.
 let fence=null;const lines=[];
 for(const line of text.replace(/\r\n?/g,'\n').split('\n')){
  const marker=line.match(/^\s{0,3}(`{3,}|~{3,})(.*)$/);
  if(fence){if(marker&&marker[1][0]===fence[0]&&marker[1].length>=fence.length&&!marker[2].trim())fence=null;continue;}
  if(marker){fence=marker[1];lines.push('A code example is shown in the conversation.');continue;}
  if(/^\s{0,3}\[[^\]]+\]:\s*\S+/.test(line))continue;
  lines.push(line.replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|\*\s+)/,''));
 }
 let plain=lines.join('\n')
  .replace(/!?\[([^\]\n]+)\]\((?:[^()]|\([^()]*\))*\)/g,'$1')
  .replace(/!?\[([^\]\n]+)\]\[[^\]\n]*\]/g,'$1')
  .replace(/<https?:\/\/[^>]+>/g,'the link shown in the conversation')
  .replace(/https?:\/\/[^\s<>]+/g,url=>'the link shown in the conversation'+(url.match(/[),.;:!?]+$/)?.[0]||''))
  .replace(/`+([^`\n]+)`+/g,'$1')
  .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g,'$2')
  .replace(/(^|[\s(])([*_])(?=\S)([^*\n]+?)\2(?=$|[\s.,!?;)])/g,'$1$3')
  .replace(/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/gm,'')
  .replace(/\s+/g,' ').trim();
 if(!plain)plain='The answer is shown in the conversation.';
 const points=Array.from(plain);if(points.length<=4000)return plain;
 const suffix=' The rest of the answer is in the conversation.';
 let prefix=points.slice(0,4000-suffix.length-1).join('');
 const sentences=[...prefix.matchAll(/[.!?](?:["'”’)]*)(?=\s)/g)];
 const boundary=sentences.at(-1);
 if(boundary&&boundary.index>=prefix.length*.5)prefix=prefix.slice(0,boundary.index+boundary[0].length);
 else {const space=prefix.lastIndexOf(' ');if(space>=prefix.length*.5)prefix=prefix.slice(0,space);prefix=prefix.trimEnd().replace(/[,;:]$/,'')+'…';}
 return prefix.trimEnd()+suffix;
}
const INTERRUPTED='Audio playback was interrupted. Choose Spoken + text and start voice chat again; your answer stays in text.';
async function resumeContext(context){
 if(context.state==='running')return;
 let timer;
 try{
  if(context.state==='closed')throw Error(INTERRUPTED);
  await Promise.race([context.resume(),new Promise((_,reject)=>{timer=root.setTimeout(()=>reject(Error(INTERRUPTED)),3000);})]);
  if(context.state!=='running')throw Error(INTERRUPTED);
 }catch{throw Error(INTERRUPTED);}finally{if(timer!==undefined)root.clearTimeout(timer);}
}
function envelope(buffer){
 const pcm=buffer.getChannelData(0),hop=Math.max(1,Math.round(buffer.sampleRate*.01));
 const rms=new Float32Array(Math.ceil(pcm.length/hop));
 for(let k=0;k<rms.length;k++){let power=0,n=0;for(let i=k*hop;i<Math.min(pcm.length,(k+2)*hop);i++){power+=pcm[i]*pcm[i];n++;}rms[k]=Math.sqrt(power/Math.max(n,1));}
 const voiced=Array.from(rms).filter(x=>x>.004).sort((a,b)=>a-b),reference=voiced[Math.floor(voiced.length*.93)]||.15;
 return Float32Array.from(rms,x=>x<.0035?0:Math.min(1,Math.pow((x-.0035)/reference,.72)));
}
class LocalVoice {
 constructor(){this.epoch=0;this.context=null;this.source=null;this.samples=null;this.start=0;this.value=0;this.last=0;this.contextListener=null;}
 async unlock(){
  const AC=root.AudioContext||root.webkitAudioContext;if(!AC)throw Error('Audio playback is unavailable. Your answer stays in text.');
  if(!this.context||this.context.state==='closed')this.context=new AC({latencyHint:'interactive'});
  await resumeContext(this.context);
 }
 stop(){this.epoch++;if(this.contextListener){const {context,listener}=this.contextListener;context.removeEventListener('statechange',listener);this.contextListener=null;}if(this.source){this.source.onended=null;try{this.source.stop();}catch{}this.source.disconnect();}this.source=null;this.samples=null;this.value=0;this.last=0;}
 async play(text,request,onstart,onend,onerror=()=>{}){
  this.stop();const epoch=this.epoch;await this.unlock();if(epoch!==this.epoch)return false;
  const result=await request('/api/speech',{text:speechText(text)});if(epoch!==this.epoch)return false;
  if(result.encoding!=='wav-base64'||typeof result.audio!=='string'||result.audio.length>14000000)throw Error('Invalid local voice response.');
  const bytes=Uint8Array.from(atob(result.audio),c=>c.charCodeAt(0));
  const context=this.context,buffer=await context.decodeAudioData(bytes.buffer);if(epoch!==this.epoch)return false;
  if(buffer.duration>300||buffer.numberOfChannels!==1)throw Error('Local voice output exceeded its limit.');
  // Synthesis/decoding can outlive a browser or device interruption.
  await resumeContext(context);if(epoch!==this.epoch)return false;
  this.samples=envelope(buffer);const source=context.createBufferSource();source.buffer=buffer;source.connect(context.destination);
  this.source=source;this.start=context.currentTime+.04;this.last=0;
  let recovering=false;
  const fail=error=>{if(epoch!==this.epoch)return;this.stop();onerror(error);};
  const listener=()=>{
   if(epoch!==this.epoch||context.state==='running'||recovering)return;
   recovering=true;
   resumeContext(context).then(()=>{recovering=false;},fail);
  };
  this.contextListener={context,listener};context.addEventListener('statechange',listener);
  source.onended=()=>{if(epoch!==this.epoch)return;if(context.state!=='running'){fail(Error(INTERRUPTED));return;}this.stop();onend();};
  source.start(this.start);onstart();return true;
 }
 level(now){
  if(!this.source||!this.samples||this.context.state!=='running')return 0;
  const context=this.context,stamp=context.getOutputTimestamp?.();
  const clock=stamp?.performanceTime>0?stamp.contextTime+Math.max(0,now-stamp.performanceTime)/1000:Math.max(0,context.currentTime-(context.outputLatency||context.baseLatency||0));
  const index=(clock-this.start)/.01,k=Math.floor(index),fraction=index-k;
  const target=k<0||k>=this.samples.length?0:this.samples[k]*(1-fraction)+(this.samples[k+1]||0)*fraction;
  const dt=this.last?Math.min(.1,(now-this.last)/1000):.016;this.last=now;
  this.value+=(target-this.value)*(1-Math.exp(-dt/(target>this.value?.018:.035)));
  if(target===0&&this.value<.003)this.value=0;return this.value;
 }
 suspend(){this.stop();if(this.context?.state==='running')this.context.suspend().catch(()=>{});}
}
root.PrometheusVoice={LocalVoice,envelope,speechText};
if(typeof module!=='undefined')module.exports=root.PrometheusVoice;
})(typeof window!=='undefined'?window:globalThis);
