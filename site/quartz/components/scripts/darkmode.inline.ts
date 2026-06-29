type Theme = "light" | "dark"

const getPreferredTheme = (): Theme => {
  const savedTheme = localStorage.getItem("theme")
  if (savedTheme === "light" || savedTheme === "dark") return savedTheme
  if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) return "dark"
  return "light"
}

const emitThemeChangeEvent = (theme: Theme) => {
  const event: CustomEventMap["themechange"] = new CustomEvent("themechange", {
    detail: { theme },
  })
  document.dispatchEvent(event)
}

const setTheme = (theme: Theme) => {
  document.documentElement.setAttribute("saved-theme", theme)
  localStorage.setItem("theme", theme)
  emitThemeChangeEvent(theme)
}

const bindThemeToggles = () => {
  document.querySelectorAll(".darkmode").forEach((toggle) => {
    if (!(toggle instanceof HTMLButtonElement) || toggle.dataset.themeBound === "true") return
    toggle.dataset.themeBound = "true"
    toggle.addEventListener("click", () => {
      const currentTheme = document.documentElement.getAttribute("saved-theme") as Theme | null
      setTheme(currentTheme === "dark" ? "light" : "dark")
    })
  })
}

setTheme(getPreferredTheme())

document.addEventListener("nav", () => {
  const theme = getPreferredTheme()
  document.documentElement.setAttribute("saved-theme", theme)
  bindThemeToggles()
  emitThemeChangeEvent(theme)
})

if (document.readyState !== "loading") bindThemeToggles()
else document.addEventListener("DOMContentLoaded", bindThemeToggles)
