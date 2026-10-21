/* A measured display policy, independent of the language-model memory budget. */
(function(root){
 'use strict';
 const names=['Still','Simple','Flow','Immersive'];
 class AdaptiveDisplay {
  constructor(){this.level=1;this.goodSince=null;this.cooldownUntil=0;this.slow=0;this.reason='Starting with lightweight motion';}
  update(s,now){
   const free=Number.isFinite(s.free)?s.free:null;
   let ceiling=free===null?1:free<.35?0:free<1.25?1:free<3?2:3;
   if(s.threads<4)ceiling=Math.min(ceiling,2);
   if(s.threads<2)ceiling=Math.min(ceiling,1);
   if(s.busy && (free===null||free<2.5))ceiling=Math.min(ceiling,1);
   if(s.hidden||s.reduced||s.preference==='still')ceiling=0;
   if(s.preference==='simple')ceiling=Math.min(ceiling,1);
   const slow=s.cost>12||s.lag>.3;
   this.slow=slow?this.slow+1:0;
   if(this.slow>=2){ceiling=Math.min(ceiling,Math.max(0,this.level-1));this.cooldownUntil=now+60000;this.slow=0;this.reason='Reduced effects after slow frames';}
   if(this.level>ceiling){this.level=ceiling;this.goodSince=null;this.cooldownUntil=Math.max(this.cooldownUntil,now+30000);this.reason=s.hidden?'Paused while hidden':s.reduced?'Reduced motion preference':s.preference!=='auto'?'Your display preference':'Reduced effects to preserve responsiveness';}
   else if(this.level<ceiling){
    const fast=s.cost<=6&&s.lag<.1;
    if(fast&&now>=this.cooldownUntil){
     if(this.goodSince===null)this.goodSince=now;
     if(now-this.goodSince>=30000){this.level++;this.goodSince=null;this.reason='More detail after 30 seconds of stable headroom';}
    }else this.goodSince=null;
   }else{this.goodSince=null;if(this.level===1&&free!==null&&free<1.25)this.reason='Simple motion keeps memory available for your work';}
   return {level:this.level,name:names[this.level],fps:[0,15,24,30][this.level],size:[0,0,420,640][this.level],reason:this.reason};
  }
 }
 const api={AdaptiveDisplay,names};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 else root.PrometheusAdaptive=api;
})(typeof window!=='undefined'?window:globalThis);
