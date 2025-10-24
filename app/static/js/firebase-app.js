import { initializeApp } from "https://www.gstatic.com/firebasejs/9.23.0/firebase-app.js";
import {
  getAuth,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signOut as fbSignOut,
  onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/9.23.0/firebase-auth.js";

window.addEventListener("load", function appInit() {
  const firebaseConfig = {
    apiKey: "AIzaSyCFKEhira5zmfoB-ddT2u9b-HlxifOjFTc",
    authDomain: "instagram-457916.firebaseapp.com",
    projectId: "instagram-457916",
    storageBucket: "instagram-457916.firebasestorage.app",
    messagingSenderId: "798283037625",
    appId: "1:798283037625:web:1e45194cc0de7844021468",
  };

  const app = initializeApp(firebaseConfig);
  const auth = getAuth(app);

  // --- helpers ---
  const setCookie = (name, value, opts = {}) => {
    const { path = "/", sameSite = "Lax", maxAge } = opts;
    let s = `${name}=${value}; Path=${path}; SameSite=${sameSite}`;
    if (typeof maxAge === "number") s += `; max-age=${maxAge}`;
    // Don't set Secure on plain http localhost
    document.cookie = s;
  };

  const deleteCookie = (name) => {
    // match the same Path and SameSite used when creating the cookie
    document.cookie = `${name}=; Path=/; max-age=0; SameSite=Lax`;
  };

  const $ = (id) => document.getElementById(id);

  console.log("Firebase initialized");

  // Keep UI in sync with Firebase auth, rather than only reading document.cookie
  onAuthStateChanged(auth, async (user) => {
    if (user) {
      try {
        const token = await user.getIdToken();
        // Keep cookie in sync (optional)
        setCookie("idToken", token);
      } catch (err) {
        console.error("Failed to refresh token:", err);
      }
    } else {
      deleteCookie("idToken");
    }
    updateUI(document.cookie);
  });

  // --- Sign Up ---
  const btnSignUp = $("sign-up");
  if (btnSignUp) {
    btnSignUp.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const email = $("email").value;
        const password = $("password").value;
        const userCredential = await createUserWithEmailAndPassword(auth, email, password);
        const token = await userCredential.user.getIdToken();
        setCookie("idToken", token);
        alert("Successful signup");
        // use replace so back button doesn't go back to signup
        window.location.replace("/");
      } catch (error) {
        console.error("Signup error:", error);
        alert("Signup failed: " + (error?.message || "unknown error"));
      }
    });
  }

  // --- Sign In ---
  const btnSignIn = $("sign-in");
  if (btnSignIn) {
    btnSignIn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const email = $("email").value;
        const password = $("password").value;
        const userCredential = await signInWithEmailAndPassword(auth, email, password);
        const token = await userCredential.user.getIdToken();
        // For local dev use Lax; in production with HTTPS you can set Secure and possibly SameSite=None
        setCookie("idToken", token);
        window.location.replace("/dashboard/"); // consistent trailing slash
      } catch (error) {
        console.error("Login error:", error);
        alert("Login error: incorrect email/password");
      }
    });
  }

  // --- Sign Out ---
  const btnSignOut = $("sign-out");
  if (btnSignOut) {
    btnSignOut.addEventListener("click", async (e) => {
      e.preventDefault();
      btnSignOut.disabled = true;
      btnSignOut.textContent = "Signing out…";
      try {
        await fbSignOut(auth);
      } catch (err) {
        console.warn("Firebase signOut failed (continuing to clear cookie):", err);
      } finally {
        deleteCookie("idToken");
        // Replace so back button doesn't re-open dashboard
        window.location.replace("/login/");
      }
    });
  }

  // --- UI toggle based on cookie (still useful as quick check) ---
  function updateUI(cookie) {
    const loginContainer =
      document.getElementById("login-container") || document.querySelector(".login-container");
    const signOutBtn = $("sign-out");

    const hasToken = cookie && cookie.includes("idToken=");
    if (hasToken) {
      if (loginContainer) loginContainer.style.display = "none";
      if (signOutBtn) signOutBtn.style.display = "inline-block";
    } else {
      if (loginContainer) loginContainer.style.display = "block";
      if (signOutBtn) signOutBtn.style.display = "none";
    }
  }

  // initial UI pass (in case onAuthStateChanged hasn't fired yet)
  updateUI(document.cookie);
});
