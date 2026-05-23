const currentTheme = "light"
document.documentElement.setAttribute("saved-theme", currentTheme)
localStorage.setItem("theme", currentTheme)

const emitThemeChangeEvent = (theme: "light") => {
  const event: CustomEventMap["themechange"] = new CustomEvent("themechange", {
    detail: { theme },
  })
  document.dispatchEvent(event)
}

document.addEventListener("nav", () => {
  document.documentElement.setAttribute("saved-theme", currentTheme)
  localStorage.setItem("theme", currentTheme)
  emitThemeChangeEvent(currentTheme)
})
