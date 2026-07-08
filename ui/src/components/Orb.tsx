import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence, useDragControls, useMotionValue } from 'framer-motion';
import { Settings, Database, Terminal, X, Mic, Activity, ChevronRight, ArrowLeft, Clock, MessageSquare } from 'lucide-react';

type JarvisRunState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';

interface OrbProps {
  state: JarvisRunState;
  transcript: string;
  recentCommands: any[];
  memory: any;
  sendCommand: (cmd: string, payload?: any) => void;
}

type PanelView = 'menu' | 'commands' | 'memory' | 'settings' | 'voice';

const STORAGE_KEY = 'jarvis-orb-position';
const PANEL_W = 280;
const PANEL_H = 300; 
const ORB_SIZE = 80;

const safeElectronAPI = {
  setIgnoreMouse: (ignore: boolean, forward: boolean) => {
    const win = window as any;
    if (win.electronAPI?.setIgnoreMouse) win.electronAPI.setIgnoreMouse(ignore, forward);
  }
};

const slideVariants = {
  enter: (dir: 1 | -1) => ({ x: dir * 40, opacity: 0 }),
  center: { x: 0, opacity: 1 },
  exit: (dir: 1 | -1) => ({ x: dir * -40, opacity: 0 }),
};

export default function Orb({ state, transcript, recentCommands, memory, sendCommand }: OrbProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [view, setView] = useState<PanelView>('menu');
  const [direction, setDirection] = useState<1 | -1>(1);
  const [isDragging, setIsDragging] = useState(false);

  const initialPos = useRef(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) return JSON.parse(saved);
    } catch {}
    return { x: window.innerWidth - 120, y: window.innerHeight - 160 };
  });

  const [position, setPosition] = useState(initialPos.current);
  const x = useMotionValue(position.x);
  const y = useMotionValue(position.y);
  const dragControls = useDragControls();

  // 🟢 NEW: Close Dashboard on OS-level clicks
  useEffect(() => {
    const handleWindowClick = () => {
      // If we click anywhere on the transparent window body, close the panel.
      // Because we set pointerEvents: 'none' on the main div, this only fires
      // if Electron passes a click through, or if we hit the transparent background.
      if (isOpen) setIsOpen(false);
    };
    window.addEventListener('click', handleWindowClick);
    return () => window.removeEventListener('click', handleWindowClick);
  }, [isOpen]);

  const getAnimation = () => {
    switch (state) {
      case 'idle': return { scale: [1, 1.04, 1], y: [0, -4, 0] };
      case 'listening': return { scale: [1, 0.8, 1.15, 1] }; 
      case 'thinking': return { rotate: [0, 180, 360], scale: [1, 0.94, 1.04, 1] };
      case 'speaking': return { scale: [1, 1.15, 0.95, 1.1, 1.02, 1.12, 1] };
      case 'error': return { x: [0, -3, 3, -3, 3, 0] };
      default: return { scale: 1 };
    }
  };

  const getTransition = () => {
    switch (state) {
      case 'idle': return { duration: 4, repeat: Infinity, ease: 'easeInOut' as const };
      case 'listening': return { duration: 0.5, ease: 'easeOut' as const };
      case 'thinking': return { rotate: { duration: 2, repeat: Infinity, ease: 'linear' as const } };
      case 'speaking': return { duration: 0.45, repeat: Infinity, repeatType: "mirror" as const, ease: "easeInOut" as const };
      default: return {};
    }
  };

  const glowColor = () => {
    switch (state) {
      case 'listening': return 'rgba(100,200,255,0.9)';
      case 'thinking': return 'rgba(160,130,255,0.8)';
      case 'speaking': return 'rgba(80,160,255,1)';
      case 'error': return 'rgba(255,80,100,0.7)';
      default: return 'rgba(100,180,255,0.35)';
    }
  };

  const panelLeft = Math.max(8, Math.min(position.x + ORB_SIZE / 2 - PANEL_W / 2, window.innerWidth - PANEL_W - 8));
  const panelTop = Math.max(8, position.y - PANEL_H - 16);

  return (
    <>
      <AnimatePresence>
        {transcript && state === 'speaking' && (
          <motion.div
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 5 }}
            className="fixed z-40 px-4 py-2 rounded-2xl bg-black/60 backdrop-blur-xl border border-white/10 text-white shadow-2xl text-xs max-w-64 text-right pointer-events-none"
            style={{
              left: Math.max(8, Math.min(position.x - 60, window.innerWidth - 260 - 8)),
              top: Math.max(8, position.y - 60),
            }}
          >
            {transcript}
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, scale: 0.9, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 20 }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            // 🟢 NEW: Stop propagation to prevent window clicks from immediately closing it
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()} 
            // 🟢 NEW: Tell Electron to intercept clicks ONLY when hovering the dashboard
            onMouseEnter={() => safeElectronAPI.setIgnoreMouse(false, false)}
            onMouseLeave={() => safeElectronAPI.setIgnoreMouse(true, true)}
            className="fixed z-30 bg-black/70 backdrop-blur-2xl border border-white/10 rounded-2xl shadow-2xl overflow-hidden flex flex-col pointer-events-auto"
            style={{ left: panelLeft, top: panelTop, width: PANEL_W, height: PANEL_H }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 bg-white/5">
              <div className="flex items-center gap-2">
                {view !== 'menu' ? (
                  <button onClick={() => { setDirection(-1); setView('menu'); }} className="text-white/50 hover:text-white/90">
                    <ArrowLeft size={16} />
                  </button>
                ) : (
                  <Activity className="text-blue-400 animate-pulse" size={16} />
                )}
                <span className="text-white/90 text-sm font-medium">Jarvis</span>
              </div>
              <button onClick={() => setIsOpen(false)} className="text-white/40 hover:text-white/80">
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto overflow-x-hidden relative">
              <AnimatePresence mode="wait" custom={direction}>
                {view === 'menu' && (
                  <motion.div key="menu" custom={direction} variants={slideVariants as any} initial="enter" animate="center" exit="exit" transition={{ type: 'spring', damping: 25 }} className="p-3 space-y-1">
                    {[
                      { icon: Mic, label: 'Voice Settings', v: 'voice' },
                      { icon: MessageSquare, label: 'Recent Commands', v: 'commands' },
                      { icon: Clock, label: 'Memory Banks', v: 'memory' },
                      { icon: Settings, label: 'System Settings', v: 'settings' },
                    ].map(({ icon: Icon, label, v }) => (
                      <button key={label} onClick={() => { setDirection(1); setView(v as PanelView); }} className="w-full flex items-center justify-between px-3 py-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors">
                        <div className="flex items-center gap-3"><Icon size={14} className="text-blue-400" /><span className="text-white/80 text-sm">{label}</span></div>
                        <ChevronRight size={14} className="text-white/30" />
                      </button>
                    ))}
                  </motion.div>
                )}
                
                {view === 'settings' && (
                  <motion.div key="settings" custom={direction} variants={slideVariants as any} initial="enter" animate="center" exit="exit" className="p-4 space-y-4">
                    <h3 className="text-white/50 text-xs font-bold uppercase tracking-wider mb-2">System Controls</h3>
                    <button 
                      onClick={(e) => {
                        const win = window as any;
                        if (win.electronAPI?.setStartup) win.electronAPI.setStartup(true);
                        e.stopPropagation();
                      }}
                      className="w-full py-2 bg-blue-500/20 text-blue-300 border border-blue-500/30 rounded-lg text-xs hover:bg-blue-500/30 transition-colors"
                    >
                      Enable Auto-Start
                    </button>
                    <button 
                      onClick={(e) => {
                        sendCommand('reboot_audio');
                        e.stopPropagation();
                      }}
                      className="w-full py-2 bg-white/5 text-white/80 border border-white/10 rounded-lg text-xs hover:bg-white/10 transition-colors"
                    >
                      Reboot Audio Engine
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.div
        className="fixed top-0 left-0 z-50 pointer-events-auto"
        style={{ x, y, width: ORB_SIZE, height: ORB_SIZE, touchAction: "none" }}
        drag dragMomentum={false} dragControls={dragControls}
        dragConstraints={{ left: 0, right: window.innerWidth - ORB_SIZE, top: 0, bottom: window.innerHeight - ORB_SIZE }}
        onDragStart={() => setIsDragging(true)}
        onDragEnd={() => {
          setIsDragging(false);
          const next = { x: x.get(), y: y.get() };
          setPosition(next);
          try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch {}
        }}
        // 🟢 NEW: Safely ignore mouse only when dashboard is closed
        onMouseEnter={() => safeElectronAPI.setIgnoreMouse(false, false)}
        onMouseLeave={() => !isOpen && safeElectronAPI.setIgnoreMouse(true, true)}
      >
        <motion.div
          animate={isDragging ? {} : getAnimation() as any}
          transition={isDragging ? { duration: 0 } : getTransition()}
          onClick={(e) => { 
            e.stopPropagation(); // 🟢 NEW: Stop click from hitting window listener
            if (!isDragging) setIsOpen(!isOpen); 
          }}
          className="w-full h-full rounded-full cursor-pointer relative flex items-center justify-center group"
          style={{ background: "radial-gradient(circle at 30% 30%, #CFEFFF 0%, #7BB8D4 60%, #3A7FA3 100%)" }}
        >
          <AnimatePresence>
            {state === 'listening' && (
              <motion.div
                initial={{ scale: 1, opacity: 0.8 }}
                animate={{ scale: 1.8, opacity: 0 }}
                transition={{ duration: 0.8, repeat: Infinity, ease: "easeOut" }}
                className="absolute inset-0 rounded-full border-[3px] border-blue-300 pointer-events-none"
              />
            )}
          </AnimatePresence>

          <div className="absolute w-8 h-8 rounded-full bg-white/30 blur-sm top-2 left-2" />
          <motion.div
            className="absolute -inset-2 rounded-full -z-10"
            animate={{ 
              boxShadow: state === 'speaking' 
                ? ["0 0 40px rgba(123,184,212,0.8)", "0 0 90px rgba(123,184,212,1)", "0 0 50px rgba(123,184,212,0.9)", "0 0 100px rgba(123,184,212,1)", "0 0 40px rgba(123,184,212,0.8)"]
                : `0 0 25px ${glowColor()}, 0 0 50px ${glowColor().replace('0.8', '0.2')}` 
            }}
            transition={state === 'speaking' ? { duration: 0.45, repeat: Infinity, repeatType: "mirror" } : { duration: 0.3 }}
          />
        </motion.div>
      </motion.div>
    </>
  );
}