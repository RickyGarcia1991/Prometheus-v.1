/* Approved Folded core material; animation is a local visual effect, not background learning. */
(function(root){
'use strict';
const vertex=`attribute vec2 a;
varying vec2 uv;
uniform float t;uniform float variant;uniform float response;uniform vec2 fit;
void main(){
 uv=vec2(.05,.075)+vec2(a.x*.5+.5,.5-a.y*.5)*vec2(.90,.665);
 vec2 p=(uv-vec2(.50,.40))/vec2(.205,.328);
 float r=length(p),angle=atan(p.y,p.x);
 float m=smoothstep(.08,.30,r)*(1.-smoothstep(.78,1.04,r));
 m*=1.-smoothstep(.72,.76,uv.y);
 float z=m*(.026*sin(angle*3.+t*.62)+.017*sin(angle*5.-t*.49));
 z*=mix(.35,1.,variant)*(1.+response*.35);
 vec2 outp=a*fit;
 outp.x+=m*.018*sin(t*.46)*z*10.;
 outp.y+=m*.011*cos(angle*3.-t*.39)*variant;
 float w=1.-z;
 gl_Position=vec4(outp,z*.12,w);
}`;
const fragment=`precision highp float;
varying vec2 uv;
uniform sampler2D art;uniform sampler2D glyphs;
uniform float t;uniform float variant;uniform float thinking;uniform float response;uniform float lighting;uniform float flowStyle;
float hash(float x){return fract(sin(x*127.1+17.23)*43758.5453);}
float wave(float a,float tm){return .031*sin(3.*a+tm*.60)+.018*sin(5.*a-tm*.47)+.009*sin(7.*a+tm*.29);}
float codeInk(vec2 grid,float seed){
 vec2 cell=fract(grid),id=floor(grid);
 float letter=mod(id.x+floor(hash(id.y+seed)*31.),32.);
 return texture2D(glyphs,vec2((letter+cell.x)/32.,cell.y)).r;
}
// Both components move: glyphs advance along curved lanes and drift inward.
// Polar coordinates follow the same deformed folds as the material underneath.
float flowingCode(vec2 q,float layer){
 vec2 cp=(q-vec2(.50,.40))/vec2(.205,.328);
 float cr=max(length(cp),.06),ca=atan(cp.y,cp.x),lr=log(cr);
 float drift=t*(.034+.010*layer);
 float spin=t*(.21+.045*layer);
 float bend=sin(ca*3.+lr*2.)*.48;
 float along=(ca-spin)*10.185916+lr*5.0;
 float across=(lr+drift)*13.+bend;
 float ink,gate;
 if(flowStyle<.5){
   vec2 grid=vec2(along*1.5,across*1.45);
   ink=codeInk(grid,layer*31.);
   gate=step(.34,hash(floor(grid.y)*5.+layer));
   return ink*gate*.58;
 }
 if(flowStyle<1.5){
   vec2 grid=vec2(along,across*.90);
   ink=codeInk(grid,layer*31.);
   float row=floor(grid.y);
   gate=step(.44,hash(row*7.+layer));
   float ribbon=pow(.5+.5*cos(fract(grid.y)*6.283185),18.);
   return (ink*.95+ribbon*.065)*gate;
 }
 vec2 grid=vec2(along*(layer>.5?1.32:1.),across*(layer>.5?1.23:1.));
 ink=codeInk(grid,layer*31.);
 gate=step(layer>.5?.52:.36,hash(floor(grid.y)*11.+layer*4.));
 return ink*gate*(layer>.5?.34:.80);
}
vec2 inwardUV(vec2 q,float phase){
 vec2 p=(q-vec2(.50,.40))/vec2(.205,.328);
 float r=length(p),a=atan(p.y,p.x);
 float mask=smoothstep(.02,.20,r)*(1.-smoothstep(.75,1.02,r));
 mask*=(1.-smoothstep(.72,.76,q.y))*smoothstep(.065,.12,q.y);
 // Sampling farther out over time makes the visible texture travel inward.
 // Two overlapping phases fade each reset out before it wraps.
 float age=phase-.5;
 float nextR=r*exp(age*.40),nextA=a-age*.55;
 vec2 transported=vec2(.50,.40)+vec2(cos(nextA),sin(nextA))*nextR*vec2(.205,.328);
 return clamp(mix(q,transported,mask),vec2(.001),vec2(.999));
}
void main(){
 vec2 p=(uv-vec2(.50,.40))/vec2(.205,.328);
 float r=length(p),a=atan(p.y,p.x);
 float ring=smoothstep(.05,.27,r)*(1.-smoothstep(.80,1.07,r));
 float protect=(1.-smoothstep(.72,.76,uv.y))*smoothstep(.065,.12,uv.y);
 ring*=protect;
 vec2 radial=p/max(r,.001),tangent=vec2(-radial.y,radial.x);
 float liquid=wave(a,t)-wave(a,0.);
 float turn=.020*sin(t*.45)+.016*(sin(2.*a-t*.38)-sin(2.*a));
 float fold=.023*(sin(2.*a+t*.58+r*4.)-sin(2.*a+r*4.))*variant;
 // Response is the measured speech envelope, not a time-driven oscillator.
 float voice=1.;
 float pulse=response*(.006+(.017+.005*lighting)*voice);
 vec2 delta=(radial*(liquid*(1.+variant*.38)+fold+pulse)+tangent*turn)*vec2(.205,.328)*ring;
 vec2 q=clamp(uv-delta,vec2(.001),vec2(.999));
 vec3 col=texture2D(art,q).rgb;
 // The approved Dimensional Fold inward flow stays active in every state.
 float idle=1.;
 if(idle>.001){
   float phase0=fract(t*.060),phase1=fract(t*.060+.5);
   float blend=1.-abs(2.*phase0-1.);
   vec3 transported=mix(texture2D(art,inwardUV(q,phase1)).rgb,texture2D(art,inwardUV(q,phase0)).rgb,blend);
   col=mix(col,transported,idle*.84);
 }
 float light=max(col.r,max(col.g,col.b));
 float sweep=pow(.5+.5*sin(a*2.4-t*1.5+r*3.8),7.);
 vec3 hue=mix(vec3(.15,1.,.56),vec3(.12,.78,1.),.5+.5*sin(a-t*.64));
 hue=mix(hue,vec3(.68,.42,1.),(.5+.5*cos(a*1.7+t*.41))*.46);
 float traveling=pow(.5+.5*sin(a*2.+r*7.-t*1.7),3.);
 float coverage=lighting>.5&&lighting<1.5?(.25+.75*traveling):1.;
 float power=lighting<.5?.58:(lighting<1.5?.85:1.05);
 col*=1.+ring*(.08*sin(a*3.+t*.7)+response*power*(.25+.75*voice)*coverage);
 col+=hue*ring*light*sweep*(.12+response*(.30+.10*lighting)*(.4+.6*voice));
 float cloud=exp(-dot(p,p)*1.05);
 // Background Matrix stays outside the orb silhouette, including its dark folds.
 float silhouette=1.-smoothstep(.97,1.16,r);
 float behind=(1.-smoothstep(.13,.55,light))*(1.-silhouette);
 col+=vec3(.035,.21,.10)*thinking*behind*cloud*(.72+.28*sin(t*1.1+p.x*2.));
 float upper=smoothstep(.06,.13,uv.y)*(1.-smoothstep(.65,.735,uv.y));
 float cols=60.;float column=floor(uv.x*cols);float speed=.042+hash(column)*.077;
 float line=uv.y*38.-t*speed*38.;float row=floor(line);
 vec2 cell=vec2(fract(uv.x*cols),fract(line));
 float letter=floor(hash(column*33.+row*.17)*32.);
 float ink=texture2D(glyphs,vec2((letter+cell.x)/32.,cell.y)).r;
 float trail=pow(.5+.5*sin(uv.y*6.-t*(.7+hash(column))+hash(column)*18.),6.);
 float specks=.34+.66*hash(column*4.+floor(row*.2));
 col+=vec3(.11,1.,.43)*ink*trail*specks*upper*behind*thinking*.67;
 // Real animated glyph layer; no frozen rain is baked into the clean artwork.
 float surface=smoothstep(.20,.40,r)*(1.-smoothstep(.96,1.13,r))*protect;
 surface*=smoothstep(.065,.31,light);
 float code=flowingCode(q,0.);
 if(flowStyle>1.5)code+=flowingCode(q,1.);
 vec3 codeColor=mix(hue,normalize(col+vec3(.02)),.55);
 col+=codeColor*code*surface*(.43+response*.19*(.3+.7*voice));
 float arc=exp(-pow((r-1.02)*27.,2.));
 col+=hue*arc*protect*response*(.08+.055*lighting)*voice;
 float halo=exp(-pow((r-.9)*3.2,2.))*protect*(1.-smoothstep(.12,.6,light));
 col+=hue*halo*response*(.035+.035*lighting)*(.25+.75*voice);
 if(idle>.001){
   float inner=smoothstep(.13,.29,r)*(1.-smoothstep(.77,1.14,r))*protect;
   float logR=log(max(r,.12));
   // Constant-phase features satisfy d(log r)/dt < 0: inward, not outward.
   float spiral=pow(.5+.5*cos(a*4.+logR*8.+t*.76),20.);
   float packets=pow(.5+.5*cos(logR*29.+t*2.8),12.);
   float soft=1.-smoothstep(.24,.85,light);
   col+=hue*idle*inner*soft*spiral*(.06+.27*packets);
 }
 float edge=smoothstep(.075,.094,uv.y)*(1.-smoothstep(.708,.740,uv.y));
 edge*=smoothstep(.05,.13,uv.x)*(1.-smoothstep(.87,.95,uv.x));
 col=mix(vec3(.0039,.0196,.0235),col,edge);
 gl_FragColor=vec4(col,1.);
}`;
  function atlas(){
    const c=document.createElement('canvas');c.width=1024;c.height=40;const x=c.getContext('2d');x.fillStyle='black';x.fillRect(0,0,c.width,c.height);x.fillStyle='white';x.font='bold 26px monospace';x.textAlign='center';x.textBaseline='middle';
    [...'if(x){run();}flow=>data[0];<code/>'].slice(0,32).forEach((v,i)=>x.fillText(v,i*32+16,20));return c;
  }

class FoldedCore {
 constructor(canvas){
  this.canvas=canvas;this.ready=false;this.failed=false;this.time=0;this.previous=0;this.level=0;this.hidden=false;
  this.image=new Image();
  this.image.onload=()=>{try{this.init();this.draw(0,'idle',0);}catch{this.failed=true;canvas.style.opacity='0';canvas.dataset.renderer='still';}};
  this.image.onerror=()=>{this.failed=true;canvas.dataset.renderer='still';};
  this.image.src='/folded-core.webp';
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();this.ready=false;canvas.style.opacity='0';canvas.dataset.renderer='still';});
  canvas.addEventListener('webglcontextrestored',()=>{try{this.init();}catch{this.failed=true;}});
 }
 init(){const canvas=this.canvas,image=this.image;

    const gl=canvas.getContext('webgl',{alpha:false,antialias:true,powerPreference:'low-power'});
    if(!gl)throw new Error('WebGL unavailable');
    const program=gl.createProgram();
    for(const [kind,code] of [[gl.VERTEX_SHADER,vertex],[gl.FRAGMENT_SHADER,fragment]]){const s=gl.createShader(kind);gl.shaderSource(s,code);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));gl.attachShader(program,s);gl.deleteShader(s);}
    gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program));gl.useProgram(program);
    const points=[],n=64,m=44;
    for(let y=0;y<m;y++)for(let x=0;x<n;x++){const x0=x/n*2-1,x1=(x+1)/n*2-1,y0=y/m*2-1,y1=(y+1)/m*2-1;points.push(x0,y0,x1,y0,x0,y1,x0,y1,x1,y0,x1,y1);}
    const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(points),gl.STATIC_DRAW);const pos=gl.getAttribLocation(program,'a');gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,2,gl.FLOAT,false,0,0);
    for(const [unit,name,bitmap]of [[0,'art',image],[1,'glyphs',atlas()]]){const tex=gl.createTexture();gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(gl.TEXTURE_2D,tex);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,bitmap);gl.uniform1i(gl.getUniformLocation(program,name),unit);}
    const u=Object.fromEntries(['t','variant','thinking','response','lighting','flowStyle','fit'].map(k=>[k,gl.getUniformLocation(program,k)]));

  this.gl=gl;this.program=program;this.u=u;this.count=points.length/2;this.ready=true;this.failed=false;canvas.dataset.renderer='webgl';
 }
 configure(level){this.level=level;this.previous=0;if(level===0){this.draw(0,'idle',0);this.canvas.style.opacity='0';}}
 draw(now,state,amplitude){
  if(!this.ready||!this.level)return;
  if(this.previous)this.time+=Math.min(.12,Math.max(0,(now-this.previous)/1000));this.previous=now;
  const {canvas,gl,program,u}=this,b=canvas.getBoundingClientRect(),cap=[0,640,850,1100][this.level]||640;
  const w=Math.max(1,Math.min(cap,Math.round(b.width*Math.min(root.devicePixelRatio||1,1.5)))),h=Math.max(1,Math.round(w/2.16376));
  if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}
  const response=state==='speaking'?Math.min(1,Math.max(0,amplitude||0))*.95:0;
  gl.viewport(0,0,w,h);gl.clearColor(.0039,.0196,.0235,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.useProgram(program);
  for(const [key,value] of Object.entries({t:this.time,variant:1,thinking:state==='thinking'?1:0,response,lighting:2,flowStyle:2}))gl.uniform1f(u[key],value);
  gl.uniform2f(u.fit,1,1);gl.drawArrays(gl.TRIANGLES,0,this.count);
  canvas.style.opacity='1';canvas.dataset.phase=state;canvas.dataset.speechLevel=response.toFixed(4);canvas.dataset.time=this.time.toFixed(3);
 }
 pause(){this.previous=0;}
}
root.PrometheusOrb={FoldedCore};
})(window);
