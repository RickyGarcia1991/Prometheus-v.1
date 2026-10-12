/* Local PCM playback and a pulse measured from that same audible stream. */
(function(root){
'use strict';
function envelope(buffer){
 const pcm=buffer.getChannelData(0),hop=Math.max(1,Math.round(buffer.sampleRate*.01));
 const rms=new Float32Array(Math.ceil(pcm.length/hop));
 for(let k=0;k<rms.length;k++){let power=0,n=0;for(let i=k*hop;i<Math.min(pcm.length,(k+2)*hop);i++){power+=pcm[i]*pcm[i];n++;}rms[k]=Math.sqrt(power/Math.max(n,1));}
 const voiced=Array.from(rms).filter(x=>x>.004).sort((a,b)=>a-b),reference=voiced[Math.floor(voiced.length*.93)]||.15;
 return Float32Array.from(rms,x=>x<.0035?0:Math.min(1,Math.pow((x-.0035)/reference,.72)));
}
class LocalVoice {
 constructor(){this.epoch=0;this.context=null;this.source=null;this.samples=null;this.start=0;this.value=0;this.last=0;}
 async unlock(){
  const AC=root.AudioContext||root.webkitAudioContext;if(!AC)throw Error('Audio playback is unavailable. Your answer stays in text.');
  if(!this.context||this.context.state==='closed')this.context=new AC({latencyHint:'interactive'});
  await this.context.resume();if(this.context.state!=='running')throw Error('Choose Spoken + text to enable local voice playback.');
 }
 stop(){this.epoch++;if(this.source){this.source.onended=null;try{this.source.stop();}catch{}this.source.disconnect();}this.source=null;this.samples=null;this.value=0;this.last=0;}
 async play(text,request,onstart,onend){
  this.stop();const epoch=this.epoch;await this.unlock();if(epoch!==this.epoch)return false;
  const result=await request('/api/speech',{text:Array.from(text).slice(0,4000).join('')});if(epoch!==this.epoch)return false;
  if(result.encoding!=='wav-base64'||typeof result.audio!=='string'||result.audio.length>14000000)throw Error('Invalid local voice response.');
  const bytes=Uint8Array.from(atob(result.audio),c=>c.charCodeAt(0));
  const context=this.context,buffer=await context.decodeAudioData(bytes.buffer);if(epoch!==this.epoch)return false;
  if(buffer.duration>300||buffer.numberOfChannels!==1)throw Error('Local voice output exceeded its limit.');
  this.samples=envelope(buffer);const source=context.createBufferSource();source.buffer=buffer;source.connect(context.destination);
  this.source=source;this.start=context.currentTime+.04;this.last=0;
  source.onended=()=>{if(epoch!==this.epoch)return;this.stop();onend();};
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
root.PrometheusVoice={LocalVoice,envelope};
if(typeof module!=='undefined')module.exports=root.PrometheusVoice;
})(typeof window!=='undefined'?window:globalThis);
