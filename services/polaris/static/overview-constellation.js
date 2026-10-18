/* Screenshot-reference Overview. React bindings are supplied by the reference bundle. */
function ze({onAgent}) {
  const canvas = C.useRef(null);
  const [paused, setPaused] = C.useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  const pauseRef = C.useRef(paused);
  C.useEffect(() => { pauseRef.current = paused; }, [paused]);
  C.useEffect(() => {
    const el = canvas.current, ctx = el.getContext('2d');
    if (!ctx) return;
    let width=0, height=0, frame=0, last=0, time=0;
    const resize = () => {
      width=el.clientWidth; height=el.clientHeight;
      const ratio=Math.min(devicePixelRatio || 1,2);
      el.width=Math.round(width*ratio); el.height=Math.round(height*ratio);
      ctx.setTransform(ratio,0,0,ratio,0,0);
    };
    const observer=new ResizeObserver(resize); observer.observe(el); resize();
    const draw = now => {
      if(last && !pauseRef.current) time+=Math.min(now-last,50)*0.00012;
      last=now;
      const w=width,h=height,cx=w*.5,cy=h*.47;
      ctx.clearRect(0,0,w,h);
      for(let j=0;j<90;j++) {
        const x=((Math.sin(j*127.1)*43758.5%1+1)%1)*w;
        const y=((Math.sin(j*269.5)*17253.5%1+1)%1)*h;
        ctx.fillStyle=`rgba(159,186,223,${.1+j%5*.035})`;
        ctx.beginPath();ctx.arc(x,y,j%9===0?1.2:.65,0,Math.PI*2);ctx.fill();
      }
      const glow=ctx.createRadialGradient(cx,cy,0,cx,cy,w*.3);
      glow.addColorStop(0,'#23365e36');glow.addColorStop(1,'#17233500');
      ctx.fillStyle=glow;ctx.fillRect(0,0,w,h);
      const rings=[
        {rx:.15,ry:.13,tilt:-.22,color:'#7599ec',phase:1.35,dots:['#82a7ff','#e4d279']},
        {rx:.205,ry:.265,tilt:.22,color:'#84cec3',phase:3.9,dots:['#ffb86d','#ee90cd']},
        {rx:.255,ry:.225,tilt:-.36,color:'#a997d8',phase:2.7,dots:['#c28aff','#68decd']}
      ];
      const point=(ring,angle)=>{
        const x=Math.cos(angle)*w*ring.rx,y=Math.sin(angle)*h*ring.ry;
        return [cx+x*Math.cos(ring.tilt)-y*Math.sin(ring.tilt),cy+x*Math.sin(ring.tilt)+y*Math.cos(ring.tilt)];
      };
      rings.forEach((ring,index)=>{
        ctx.beginPath();
        for(let step=0;step<=200;step++){
          const [x,y]=point(ring,step/200*Math.PI*2);step?ctx.lineTo(x,y):ctx.moveTo(x,y);
        }
        ctx.strokeStyle=ring.color+'40';ctx.lineWidth=1.1;ctx.stroke();
        ring.dots.forEach((color,j)=>{
          const angle=ring.phase+time*(index===1?-.65:.65)+j*Math.PI;
          const [x,y]=point(ring,angle);
          const left=j===0, labelY=[.25,.5,.74][index]*h;
          const line=ctx.createLinearGradient(left?w*.225:w*.775,labelY,x,y);
          line.addColorStop(0,color+'08');line.addColorStop(1,color+'45');
          ctx.beginPath();ctx.moveTo(left?w*.225:w*.775,labelY);ctx.lineTo(x,y);
          ctx.strokeStyle=line;ctx.lineWidth=1;ctx.stroke();
          ctx.fillStyle=color;ctx.shadowColor=color;ctx.shadowBlur=22;
          ctx.beginPath();ctx.arc(x,y,Math.max(4,Math.min(8,w*.0052)),0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;
        });
      });
      frame=requestAnimationFrame(draw);
    };
    frame=requestAnimationFrame(draw);
    return()=>{cancelAnimationFrame(frame);observer.disconnect();};
  },[]);
  const positions={Rosetta:[16,25],Hubble:[16,49],Kepler:[16,73],Spectra:[84,25],Pleiades:[84,49],Horizon:[84,73]};
  return I.jsxs('section',{className:'reference-constellation','aria-label':'The ZAR constellation',children:[
    I.jsx('canvas',{ref:canvas,'aria-hidden':true}),
    I.jsxs('div',{className:'constellation-heading',children:[I.jsx('span',{className:'eyebrow',children:'THE ZAR CONSTELLATION'}),I.jsx('span',{className:'micro',children:'CORE 3.2 / RETAIL PACK'})]}),
    I.jsx('div',{className:'constellation-labels',children:Ee.filter(agent=>agent.id!=='Polaris').map(agent=>I.jsxs('button',{type:'button',className:'constellation-label',style:{left:positions[agent.id][0]+'%',top:positions[agent.id][1]+'%','--agent-color':agent.color},'aria-label':`${agent.id} — ${agent.role}`,onClick:()=>onAgent(agent.id),children:[I.jsx('span',{className:'constellation-name',children:agent.id}),I.jsx('span',{className:'constellation-role',children:agent.role})]},agent.id))}),
    I.jsxs('button',{type:'button',className:'constellation-core','aria-label':'Polaris — Orchestrator',onClick:()=>onAgent('Polaris'),children:[I.jsx('span',{className:'constellation-sphere',children:'ZAR'}),I.jsx('span',{className:'constellation-core-name',children:'POLARIS'}),I.jsx('span',{className:'constellation-role',children:'MASTER ORCHESTRATOR'})]}),
    I.jsxs('div',{className:'constellation-footer',children:[I.jsx('span',{children:'One intent. The right intelligence.'}),I.jsx('button',{type:'button',className:'icon-button','aria-label':paused?'Play orbit':'Pause orbit',onClick:()=>setPaused(!paused),children:I.jsx(paused?le:ce,{size:18})})]})
  ]});
}
