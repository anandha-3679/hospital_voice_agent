export default function OrbAnimation({ active, speaking }) {
  return (
    <div className="relative flex items-center justify-center w-36 h-36">

      {/* Pulse rings: violet when mic active, teal when speaking */}
      {active && !speaking && (
        <>
          <span className="absolute inset-0 rounded-full bg-indigo-500/20 animate-ping" />
          <span className="absolute inset-4 rounded-full bg-indigo-400/15 animate-ping [animation-delay:0.3s]" />
        </>
      )}
      {speaking && (
        <>
          <span className="absolute inset-0 rounded-full bg-teal-400/20 animate-ping" />
          <span className="absolute inset-4 rounded-full bg-teal-300/15 animate-ping [animation-delay:0.25s]" />
        </>
      )}

      {/* Core orb */}
      <div
        className={`w-24 h-24 rounded-full transition-all duration-500
          ${speaking
            ? 'bg-gradient-to-br from-teal-300 via-cyan-400 to-blue-500 shadow-[0_0_60px_rgba(45,212,191,0.6)] scale-110'
            : active
              ? 'bg-gradient-to-br from-indigo-400 via-violet-500 to-blue-600 shadow-[0_0_60px_rgba(139,92,246,0.7)] scale-110'
              : 'bg-gradient-to-br from-indigo-400 via-violet-500 to-blue-600 shadow-[0_0_40px_rgba(99,102,241,0.5)] scale-100'
          }`}
      />
    </div>
  )
}
