/* Lightweight ribbons display measured audio only; no devices or textures. */
(function(root){
'use strict';
class FoldedCore {
 constructor(canvas){this.canvas=canvas;this.level=1;this.context=canvas.getContext('2d',{alpha:true});this.ready=!!this.context;canvas.dataset.renderer=this.ready?'canvas2d':'still';this.draw(0,'idle',0);}
 configure(level){this.level=level;this.draw(0,'idle',0);}
 draw(now,state,amplitude){
  if(!this.context)return;
  const canvas=this.canvas,ctx=this.context,box=canvas.getBoundingClientRect();
  const ratio=Math.min(1.5,Math.max(1,root.devicePixelRatio||1));
  const width=Math.max(1,Math.min(960,Math.round(box.width*ratio))),height=Math.max(1,Math.min(260,Math.round(box.height*ratio)));
  if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
  const measured=['speaking','listening','finishing'].includes(state)&&this.level>0&&Number.isFinite(amplitude)?Math.max(0,Math.min(1,amplitude)):0;
  ctx.clearRect(0,0,width,height);
  const colors=[['#58cda466','#b5fff0','#76bddd22'],['#9e8ac922','#cfb8f0aa','#85dccc22'],['#64b6d422','#93dde5aa','#8fc3d322']];
  for(let layer=2;layer>=0;layer--){
   const gradient=ctx.createLinearGradient(0,0,width,0);gradient.addColorStop(0,colors[layer][0]);gradient.addColorStop(.5,colors[layer][1]);gradient.addColorStop(1,colors[layer][2]);
   ctx.beginPath();
   for(let point=0;point<=96;point++){
    const u=point/96,envelope=Math.pow(Math.sin(Math.PI*u),2);
    const shape=Math.sin(u*Math.PI*6-now*.002+layer*.65)*.7+Math.sin(u*Math.PI*10+now*.0013+layer)*.3;
    const y=height/2+(layer-1)*2+shape*envelope*measured*height*(.31-layer*.035);
    if(point===0)ctx.moveTo(u*width,y);else ctx.lineTo(u*width,y);
   }
   ctx.strokeStyle=gradient;ctx.lineWidth=layer===0?2.2:1.2;ctx.stroke();
  }
  canvas.style.opacity='1';canvas.dataset.phase=state;canvas.dataset.speechLevel=measured.toFixed(4);
 }
 pause(){}
}
// A short history of measured levels. No clock-driven animation or microphone access.
class VoiceWaveform {
 constructor(element,sourceLabel){
  this.element=element;this.sourceLabel=sourceLabel;this.state=null;
  this.values=new Float32Array(32);this.lastSample=-Infinity;
  this.bars=Array.from({length:this.values.length},()=>{const bar=document.createElement('span');element.append(bar);return bar;});
  this.setState('idle');
 }
 setState(state){
  const labels={idle:'Microphone off',opening:'Starting microphone',listening:'Microphone input',finishing:'Closing microphone',transcribing:'Transcribing · microphone off',thinking:'Thinking · microphone off',preparing:'Preparing voice · microphone off',speaking:'Voice output · microphone off',error:'Microphone off',offline:'Microphone off'};
  const next=Object.hasOwn(labels,state)?state:'idle';
  if(next===this.state)return;
  this.state=next;this.element.dataset.phase=next;
  if(this.sourceLabel)this.sourceLabel.textContent=labels[next];
  this.reset();
 }
 reset(){
  this.values.fill(0);this.lastSample=-Infinity;
  for(const bar of this.bars){bar.style.height='3px';bar.style.opacity='.32';}
 }
 sample(level,now,source='input'){
  const expected=this.state==='speaking'?'output':['listening','finishing'].includes(this.state)?'input':null;
  if(source!==expected)return false;
  // Playback can provide a frame every refresh; twenty visual samples per second suffice.
  const time=Number.isFinite(now)?now:0;
  if(source==='output'&&time>=this.lastSample&&time-this.lastSample<50)return false;
  this.lastSample=time;
  this.values.copyWithin(0,1);this.values[this.values.length-1]=Number.isFinite(level)?Math.min(1,Math.max(0,level)):0;
  for(let i=0;i<this.bars.length;i++){
   this.bars[i].style.height=(3+37*this.values[i])+'px';
   this.bars[i].style.opacity=String(.32+.68*this.values[i]);
  }
  return true;
 }
}
root.PrometheusOrb={FoldedCore,VoiceWaveform};
})(window);
