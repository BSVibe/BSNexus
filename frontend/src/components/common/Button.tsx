import { forwardRef } from 'react'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'icon'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
}

const sizeClasses = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2 text-sm',
  lg: 'px-5 py-2.5 text-base',
}

const variantClasses = {
  primary: 'bg-gradient-to-r from-stitch-primary to-stitch-primary-container text-stitch-on-primary-container font-bold shadow-lg shadow-stitch-primary/20 hover:opacity-90',
  secondary: 'bg-stitch-surface-highest text-text-primary hover:bg-stitch-surface-high hover:opacity-80',
  ghost: 'bg-transparent text-text-secondary hover:bg-stitch-surface-container',
  icon: 'bg-transparent text-text-tertiary hover:bg-stitch-surface-container p-2',
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', loading, className = '', children, disabled, ...props }, ref) => {
    const isIcon = variant === 'icon'
    const sizeClass = isIcon ? '' : sizeClasses[size]
    const variantClass = variantClasses[variant]

    return (
      <button
        ref={ref}
        className={`inline-flex items-center justify-center rounded-md font-semibold transition-all
          disabled:opacity-50 disabled:cursor-not-allowed
          ${variantClass} ${sizeClass} ${className}`}
        disabled={disabled || loading}
        {...props}
      >
        {loading && (
          <svg className="animate-spin -ml-1 mr-2 h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        )}
        {children}
      </button>
    )
  }
)

Button.displayName = 'Button'

export { Button }
export type { ButtonProps }
