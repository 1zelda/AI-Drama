export const metadata = {
  title: 'AI Drama Studio',
  description: 'AI-powered drama creation platform',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang='zh'>
      <body style={{ margin: 0, fontFamily: 'Inter, system-ui, sans-serif', background: '#0a0a0a', color: '#fff' }}>
        {children}
      </body>
    </html>
  )
}
