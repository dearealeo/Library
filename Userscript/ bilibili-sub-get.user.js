// ==UserScript==
// @name        Bilibili Subtitle Downloader
// @description A fork of learnerLj/bilibili-subtitle with optimized features
// @namespace   https://github.com/dearealeo
// @match       https://www.bilibili.com/video/*
// @version     1.2.0
// @author      dearealeo <https://github.com/dearealeo>
// @grant       none
// ==/UserScript==

(() => {
  "use strict";

  const domReadyObserver = new MutationObserver((_, observer) => {
    if (
      document.readyState === "complete" &&
      !document.querySelector("#subtitle-download-container")
    ) {
      initializeDownloadButton();
      observer.disconnect();
    }
  });

  domReadyObserver.observe(document, { childList: true, subtree: true });

  if (
    document.readyState === "complete" &&
    !document.querySelector("#subtitle-download-container")
  ) {
    initializeDownloadButton();
  }

  function initializeDownloadButton() {
    if (document.querySelector("#subtitle-download-container")) return;

    const fragment = document.createDocumentFragment();

    const downloadContainer = document.createElement("div");
    downloadContainer.id = "subtitle-download-container";
    Object.assign(downloadContainer.style, {
      position: "fixed",
      left: "0",
      top: "50%",
      transform: "translateY(-50%)",
      backgroundColor: "rgba(251, 114, 153, 0.7)",
      color: "white",
      padding: "5px 8px",
      borderRadius: "0 4px 4px 0",
      cursor: "pointer",
      zIndex: "999",
      display: "flex",
      alignItems: "center",
      boxShadow: "2px 2px 10px rgba(0, 0, 0, 0.2)",
      transition: "all 0.3s ease",
      fontSize: "12px",
    });

    const downloadIcon = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "svg"
    );
    downloadIcon.setAttribute("width", "10");
    downloadIcon.setAttribute("height", "10");
    downloadIcon.setAttribute("viewBox", "0 0 24 24");
    downloadIcon.setAttribute("fill", "none");
    downloadIcon.style.marginRight = "4px";
    downloadIcon.id = "subtitle-download-icon";
    downloadIcon.innerHTML = `<path fill-rule="evenodd" clip-rule="evenodd" d="M12 4C12.5523 4 13 4.44772 13 5V13.5858L15.2929 11.2929C15.6834 10.9024 16.3166 10.9024 16.7071 11.2929C17.0976 11.6834 17.0976 12.3166 16.7071 12.7071L12.7071 16.7071C12.3166 17.0976 11.6834 17.0976 11.2929 16.7071L7.29289 12.7071C6.90237 12.3166 6.90237 11.6834 7.29289 11.2929C7.68342 10.9024 8.31658 10.9024 8.70711 11.2929L11 13.5858V5C11 4.44772 11.4477 4 12 4ZM4 14C4.55228 14 5 14.4477 5 15V17C5 17.5523 5.44772 18 6 18H18C18.5523 18 19 17.5523 19 17V15C19 14.4477 19.4477 14 20 14C20.5523 14 21 14.4477 21 15V17C21 18.6569 19.6569 20 18 20H6C4.34315 20 3 18.6569 3 17V15C3 14.4477 3.44772 14 4 14Z" fill="white"/>`;

    const buttonLabel = document.createElement("span");
    buttonLabel.textContent = "Download Subtitles";
    buttonLabel.style.fontSize = "12px";
    buttonLabel.id = "subtitle-download-text";

    downloadContainer.appendChild(downloadIcon);
    downloadContainer.appendChild(buttonLabel);

    downloadContainer.addEventListener("click", processSubtitles);

    fragment.appendChild(downloadContainer);
    document.body.appendChild(fragment);
  }

  function processSubtitles() {
    const downloadContainer = document.querySelector(
      "#subtitle-download-container"
    );
    const buttonLabel = document.querySelector("#subtitle-download-text");

    if (!downloadContainer || !buttonLabel) return;

    const originalText = buttonLabel.textContent;
    const originalColor = downloadContainer.style.backgroundColor;

    buttonLabel.textContent = "Downloading...";
    downloadContainer.style.backgroundColor = "rgba(251, 114, 153, 0.9)";

    if (!document.getElementById("subtitle-loading-style")) {
      const loadingAnimation = document.createElement("style");
      loadingAnimation.id = "subtitle-loading-style";
      loadingAnimation.textContent =
        "@keyframes pulse{0%{opacity:.2}50%{opacity:1}100%{opacity:.2}}";
      document.head.appendChild(loadingAnimation);
    }

    const loadingIndicator = document.createElement("span");
    loadingIndicator.textContent = " •";
    loadingIndicator.style.animation = "pulse 1s infinite";
    loadingIndicator.id = "loading-dot";
    buttonLabel.appendChild(loadingIndicator);

    const aiAssistantButton = document.querySelector(".video-ai-assistant");
    if (!aiAssistantButton) {
      updateUIState(
        "Failed to find AI assistant button. Please ensure you are on a Bilibili video page."
      );
      return;
    }

    aiAssistantButton.click();

    const subtitleExtractionPromise = new Promise((resolve, reject) => {
      setTimeout(() => {
        try {
          const subtitleListButton = findSubtitleListButton();
          if (!subtitleListButton) {
            reject(new Error("Failed to find subtitle list button."));
            return;
          }

          subtitleListButton.click();

          setTimeout(() => {
            try {
              let subtitles = extractSubtitlesWithPrimarySelector();

              if (subtitles.length === 0) {
                subtitles = extractSubtitlesWithFallbackSelectors();
              }

              if (subtitles.length === 0) {
                reject(new Error("Failed to extract subtitles."));
                return;
              }

              resolve(subtitles);
            } catch (error) {
              reject(error);
            }
          }, 1500);
        } catch (error) {
          reject(error);
        }
      }, 1500);
    });

    subtitleExtractionPromise
      .then(subtitles => {
        const uniqueSubtitles = [...new Set(subtitles)];
        const subtitleContent = uniqueSubtitles.join("\n");

        return navigator.clipboard.writeText(subtitleContent).then(() => {
          showNotification(
            `Copied ${uniqueSubtitles.length} subtitle lines to clipboard.`,
            downloadContainer
          );
          updateUIState(null, originalColor, originalText);
          return true;
        });
      })
      .catch(error => {
        updateUIState(
          error.message || "Unexpected error extracting subtitles."
        );
        return false;
      })
      .finally(() => {
        const closeButton = document.querySelector(".close-btn");
        if (closeButton) closeButton.click();
      });
  }

  function extractSubtitlesWithPrimarySelector() {
    const subtitles = [];
    document.querySelectorAll("._Part_1iu0q_16").forEach(subtitleEntry => {
      const timeElem = subtitleEntry.querySelector("._TimeText_1iu0q_35");
      const textElem = subtitleEntry.querySelector("._Text_1iu0q_64");

      if (timeElem && textElem) {
        subtitles.push(`${timeElem.textContent}: ${textElem.textContent}`);
      }
    });
    return subtitles;
  }

  function extractSubtitlesWithFallbackSelectors() {
    const subtitles = [];
    const timeRegex = /^\d+:\d+$/;

    document
      .querySelectorAll('[class*="time"], [class*="Time"]')
      .forEach(timeElem => {
        if (timeRegex.test(timeElem.textContent.trim())) {
          const textElem = timeElem.nextElementSibling;
          if (textElem) {
            subtitles.push(`${timeElem.textContent}: ${textElem.textContent}`);
          }
        }
      });

    if (subtitles.length === 0) {
      document
        .querySelectorAll(
          '[class*="subtitle"], [class*="Subtitle"], [class*="Part"], [class*="part"], [class*="Line"], [class*="line"]'
        )
        .forEach(container => {
          const children = container.children;
          if (children.length >= 2) {
            const firstChild = children[0];
            const secondChild = children[1];

            if (firstChild && timeRegex.test(firstChild.textContent.trim())) {
              subtitles.push(
                `${firstChild.textContent}: ${secondChild.textContent}`
              );
            }
          }
        });
    }

    if (subtitles.length === 0) {
      const allSpans = document.querySelectorAll("span");
      for (let i = 0; i < allSpans.length - 1; i++) {
        if (timeRegex.test(allSpans[i].textContent.trim())) {
          subtitles.push(
            `${allSpans[i].textContent}: ${allSpans[i + 1].textContent}`
          );
        }
      }
    }

    if (subtitles.length === 0) {
      document
        .querySelectorAll(
          '[class*="text"], [class*="Text"], [class*="content"], [class*="Content"]'
        )
        .forEach(elem => {
          const text = elem.textContent.trim();
          if (text.length > 0) {
            subtitles.push(text);
          }
        });
    }

    return subtitles;
  }

  function findSubtitleListButton() {
    const buttonByClass = document.querySelector("span._Label_krx6h_18");
    if (buttonByClass?.textContent === "字幕列表") {
      return buttonByClass;
    }

    const targetText = "字幕列表";

    const buttonByText = Array.from(
      document.querySelectorAll("span, button, div")
    ).find(el => el.textContent === targetText);
    if (buttonByText) return buttonByText;

    return Array.from(
      document.querySelectorAll(
        '[class*="Label"], [class*="label"], [class*="btn"], [class*="button"]'
      )
    ).find(el => el.textContent === targetText);
  }

  function showNotification(message, referenceElement) {
    const rect = referenceElement.getBoundingClientRect();

    const notification = document.createElement("div");
    notification.textContent = message;
    Object.assign(notification.style, {
      position: "fixed",
      top: `${rect.top}px`,
      left: `${rect.right + 10}px`,
      padding: "5px 10px",
      backgroundColor: "#fb7299",
      color: "white",
      borderRadius: "4px",
      zIndex: "9999",
      fontSize: "12px",
      boxShadow: "2px 2px 10px rgba(0, 0, 0, 0.2)",
      whiteSpace: "nowrap",
    });

    document.body.appendChild(notification);

    setTimeout(() => {
      if (notification.parentNode) {
        document.body.removeChild(notification);
      }
    }, 1500);
  }

  function updateUIState(errorMessage, originalColor, originalText) {
    const downloadContainer = document.querySelector(
      "#subtitle-download-container"
    );
    const buttonLabel = document.querySelector("#subtitle-download-text");
    const loadingIndicator = document.querySelector("#loading-dot");

    if (errorMessage) {
      alert(errorMessage);
    }

    if (downloadContainer && originalColor) {
      downloadContainer.style.backgroundColor = originalColor;
    }

    if (buttonLabel && originalText) {
      buttonLabel.textContent = originalText;
    }

    if (loadingIndicator && loadingIndicator.parentNode) {
      loadingIndicator.parentNode.removeChild(loadingIndicator);
    }
  }
})();
