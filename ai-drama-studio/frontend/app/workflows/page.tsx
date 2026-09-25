'use client'
import React, { useState, useEffect } from 'react'

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<any[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [output, setOutput] = useState<any>(null)

  useEffect(() => {
    fetch('/api/workflows')
      .then(res => res.json())
      .then(data => setWorkflows(data))
  }, [])

  const executeWorkflow = async (name: string) => {
    const res = await fetch('/api/workflows/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workflow: name, input: { title: '测试标题' } })
    })
    const data = await res.json()
    setSelected(name)
    setOutput(data)
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0a', color: '#fff', padding: '2rem' }}>
      <h1 style={{ fontSize: '2rem', marginBottom: '2rem' }}>工作流管理</h1>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        <div>
          <h2>可用工作流</h2>
          {workflows.map((w: any) => (
            <div key={w.name} style={{ padding: '1rem', background: '#1a1a1a', borderRadius: '8px', marginBottom: '1rem' }}>
              <h3>{w.name}</h3>
              <p style={{ color: '#888', fontSize: '0.9rem' }}>{w.description}</p>
              <button onClick={() => executeWorkflow(w.name)} style={{ marginTop: '0.5rem', padding: '0.5rem 1rem', background: '#6366f1', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                执行
              </button>
            </div>
          ))}
        </div>
        <div>
          <h2>执行结果</h2>
          {output && (
            <pre style={{ background: '#1a1a1a', padding: '1rem', borderRadius: '8px', overflow: 'auto', maxHeight: '600px' }}>
              {JSON.stringify(output, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}
