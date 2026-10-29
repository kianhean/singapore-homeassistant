# SP Services usage in Home Assistant

This guide connects your **SP Services** account (the one you use at
[services.spservices.sg](https://services.spservices.sg/) or in the SP app) to
Home Assistant. You get:

- sensors for electricity used **today**, **this month** and **last month**, and
  water used **this month** and **last month**;
- your **half-hourly electricity history** and **monthly water history** in
  Home Assistant's Energy dashboard.

It takes about 10 minutes. You don't need to know any programming. You will
copy and paste two commands.

> **Already have a smart meter in Home Assistant** (for example a Shelly EM,
> an IHD reader or a P1 dongle)? Read
> [If you already have a smart meter](#if-you-already-have-a-smart-meter) first.

---

## How it works (the short version)

SP protects its login with a captcha and an SMS code (OTP). Home Assistant
can't fill those in, so the login happens **once, on your own computer**:

1. A small helper program opens a browser window on your computer.
2. You log in to SP there, as usual: email, password, captcha and SMS code.
3. The program prints a long line of text called a **session string**.
4. You paste that into Home Assistant.

After that, Home Assistant keeps itself logged in. It renews the login in the
background about once an hour, with no captcha or OTP. You only need to repeat
the login if Home Assistant has been switched off or offline for **more than 3
days**. Home Assistant will tell you when that happens (see
[Logging in again](#logging-in-again)).

> 🔒 **The session string works like a password.** Anyone who has it can see
> your SP usage and account details until the session ends. Don't share it,
> post it in a forum or put it in a screenshot. Home Assistant stores it with
> its other integration credentials and leaves it out of diagnostics files.

---

## What you need

- An SP Services account you can log in to, and the phone that receives its
  SMS codes.
- A **Windows 10/11** or **macOS** computer. A Linux PC works too.
- A Chromium-based browser installed on that computer: **Google Chrome**,
  **Microsoft Edge** or **Brave**. Edge is already installed on every Windows
  PC. Safari and Firefox can't be used for this step.
- Home Assistant 2025.4 or newer, with the **Singapore** integration set up
  (version 0.6.0 or newer).

---

## Step 1 — Install `uv` (one time)

`uv` is a small, free tool that downloads and runs Python programs for you.
You don't need to install Python yourself, because `uv` fetches what it needs
into its own folder. It doesn't change anything else on your computer, and you
can remove it afterwards (see [Removing uv](#removing-uv-optional)).

### Windows

1. Click **Start**, type **PowerShell**, and open **Windows PowerShell**. A blue
   or black window with a blinking cursor appears. You don't need "Run as
   administrator".
2. Copy this line, paste it into the window (right-click pastes), and press
   **Enter**:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

3. Wait until it says `everything's installed!`.
4. **Close the PowerShell window and open a new one** (repeat step 1). The new
   window is needed so it can find `uv`.
5. Check it worked. Type the following and press **Enter**:

   ```powershell
   uv --version
   ```

   You should see `uv` followed by a version number. If you see
   *"uv is not recognized"*, close every PowerShell window, open a new one and
   try again. If it still fails, restart the computer.

### Mac

1. Press **⌘ Command + Space**, type **Terminal**, and press **Return**. A
   window with a blinking cursor appears.
2. Copy this line, paste it with **⌘ Command + V**, and press **Return**:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Wait until it says `everything's installed!`.
4. **Quit Terminal (⌘ Command + Q) and open it again.**
5. Check it worked:

   ```bash
   uv --version
   ```

   You should see `uv` followed by a version number. If you see
   `command not found: uv`, quit and reopen Terminal again.

   If you use [Homebrew](https://brew.sh), `brew install uv` works too.

---

## Step 2 — Log in to SP and get your session string

Use the **same window** as Step 1: PowerShell on Windows, Terminal on Mac.

1. Copy this whole line, paste it in, and press **Enter**:

   ```text
   uvx --from "sp-services @ https://github.com/kianhean/sp_api/archive/refs/tags/v1.3.0.zip" sp-services-e2e --export-session --no-token-cache
   ```

   The first run takes a minute while `uv` downloads what it needs.

2. A **new browser window** opens on the SP login page. This is a separate,
   temporary window, so it won't touch your normal browser tabs, bookmarks or
   passwords. Log in as you usually do:
   - email and password,
   - tick the captcha ("I'm not a robot") if one appears,
   - type in the SMS code SP sends to your phone.

3. The browser window **closes by itself** once you're logged in. You don't
   need to copy anything from the browser.

4. Back in PowerShell or Terminal you'll see a warning line followed by **one
   very long line of letters and numbers**. That long line is your session
   string. Copy all of it:
   - **Windows:** select the line with the mouse and press **Enter** (or
     right-click) to copy it. Make sure you get the whole line; it's long.
   - **Mac:** select the line with the mouse and press **⌘ Command + C**.

   Keep the window open until you've pasted the string into Home Assistant
   (Step 3).

> **Nothing happened, or "no supported browser"?** Install
> [Google Chrome](https://www.google.com/chrome/) and run the command again.
> If Chrome is installed somewhere unusual, see [Troubleshooting](#troubleshooting).

---

## Step 3 — Add the account in Home Assistant

1. In Home Assistant, go to **Settings → Devices & services**.
2. Open the **Singapore** integration.
3. Click **Add SP Services account**. Depending on your Home Assistant
   version, it's at the top of the page or under the **⋮** menu.
4. Paste the session string into **Session string**.
5. Choose whether to tick **Import usage history into statistics (Energy
   dashboard)**:
   - **Tick it** (the default) if you want SP's electricity and water history
     in the Energy dashboard.
   - **Untick it** if you already measure your usage with your own smart
     meter and only want the sensors (see
     [below](#if-you-already-have-a-smart-meter)).
6. Click **Submit**.

Home Assistant checks the login, finds your SP account number and creates a
device called **SP Services \<account number\>** with five sensors:

| Sensor | Unit | Notes |
|---|---|---|
| Electricity today | kWh | SP's figure for today so far; updates every 30 minutes |
| Electricity this month | kWh | Shows *unknown* until SP publishes the month |
| Electricity last month | kWh | |
| Water this month | m³ | Shows *unknown* until SP publishes the month |
| Water last month | m³ | |

SP doesn't publish same-day water usage, so there's no "water today" sensor.

You can now close PowerShell or Terminal. The session string isn't needed on
your computer any more.

---

## Step 4 — Show your usage in the Energy dashboard

With **Import usage history into statistics** ticked, the integration adds
two long-term statistics to Home Assistant:

| Statistic | What it contains |
|---|---|
| **SP Services \<account\> electricity** (`singapore:sp_<account>_electricity`) | Hourly electricity use (kWh), built from SP's half-hourly meter data |
| **SP Services \<account\> water** (`singapore:sp_<account>_water`) | Monthly water use (m³), one value per completed month |

They show up a few minutes after you add the account. All the history SP
provides is imported, not just new data from today onwards.

> **Pick the statistic, not a sensor.** The *Electricity today / this month*
> and *Water this month* sensors are running totals that reset, so the Energy
> dashboard doesn't list them, and searching for `sensor.sp_services_…` finds
> nothing. In the Energy settings, choose the entries named
> **SP Services \<account\> electricity** and **SP Services \<account\> water**.
> To check they exist, open **Developer tools → Statistics** and search for
> `sp_`.

### Electricity

1. Go to **Settings → Dashboards → Energy**. On older versions it's
   **Settings → Energy**.
2. Under **Electricity grid**, click **Add consumption**.
3. In **Consumed energy**, pick **SP Services \<account\> electricity**.
4. *(Optional) Add the cost.* The Singapore integration's tariff sensor reports
   in **cents** per kWh, but the Energy dashboard needs **dollars** per kWh.
   You can do either of these:
   - **Simple:** choose **Use a static price** and type in the current tariff
     in dollars, e.g. `0.2972` for 29.72 ¢/kWh. You'll need to update it each
     quarter.
   - **Automatic:** create a helper that converts the tariff to dollars, then
     pick it:
     1. **Settings → Devices & services → Helpers → Create helper →
        Template → Template a sensor**.
     2. Name: `Electricity price`. State template:
        ```jinja
        {{ (states('sensor.energy_electricity_tariff') | float(0) / 100) | round(4) }}
        ```
        Unit of measurement: `SGD/kWh`. Click **Submit**.
     3. Back in the Energy settings, choose **Use an entity with current
        price** and pick **Electricity price**.

     This assumes your Home Assistant currency is set to SGD
     (**Settings → System → General → Currency**).
5. Click **Save**.

### Water

1. In the same Energy settings page, under **Water consumption**, click
   **Add water source**.
2. Pick **SP Services \<account\> water**.
3. *(Optional)* For the cost, choose **Use an entity with current price** and
   pick the integration's **Water Tariff** sensor. It's already in SGD/m³.
4. Click **Save**.

### What to expect

- **Electricity runs behind real time.** SP publishes half-hourly readings
  after a delay (typically hours, sometimes up to a day), so recent hours fill
  in later. The integration
  checks every 30 minutes and adds new hours as they appear. It never adds an
  hour twice, so nothing is double counted.
- **Water is monthly.** SP only publishes water per month, so each month's
  total appears as one bar on the **1st of that month**, once SP has published
  the month. Use the **Month** or **Year** view to see water. The Day and Week
  views will look empty.
- **Readings marked *Estimated* by SP** are imported as SP published them. The
  *this month* / *last month* sensors follow SP if it later corrects an
  estimate. Hours and months already in the Energy dashboard are not
  re-imported.
- **"Electricity today" may not match the Energy dashboard's "today"
  exactly.** The sensor shows SP's running total; the dashboard only shows
  completed hours.

---

## If you already have a smart meter

If Home Assistant already measures your electricity, for example with a clamp
meter, a smart plug setup or a meter reader, this integration **does not
touch** it:

- SP data goes into **its own** statistics and sensors. Your existing sensors
  and their history are never changed.
- Nothing is added to the Energy dashboard until **you** pick the SP statistic
  there.

Here's how to choose:

| You want… | Do this |
|---|---|
| Keep your smart meter as the source of truth | Keep your meter in **Electricity grid**. **Don't** also add the SP statistic there, or your usage will be **counted twice**. |
| Compare your meter against what SP bills | Leave the SP statistic out of the Energy dashboard. Plot both in a **Statistics graph** card on any dashboard. |
| See usage from before you had the meter | Use the SP statistic. It has SP's past readings. |
| Only the monthly sensors, no history at all | Untick **Import usage history into statistics** (Step 3, or later under **Reconfigure**). |

A local smart meter updates in real time; SP's data comes from your SP meter
with a delay of hours to a day. SP's numbers are the ones on your bill.

Water is different: most homes have no water sensor in Home Assistant, so the
SP water statistic is usually the only water source you'll have.

---

## Logging in again

Home Assistant renews the login on its own while it's running. You'll only
need to log in again if:

- Home Assistant was off or offline for **more than 3 days**, or
- SP ended the session on their side, for example after a password change or
  "log out of all devices".

When that happens, the SP sensors become *unavailable* and a message appears
under **Settings → Repairs**: *"SP Services account … needs you to log in
again"*. To fix it:

1. Repeat [Step 2](#step-2--log-in-to-sp-and-get-your-session-string) on your
   computer. `uv` is already installed, so just run the `uvx …` command again.
2. In Home Assistant go to **Settings → Devices & services → Singapore**, find
   your **SP Services** account, open its **⋮** menu and choose
   **Reconfigure**.
3. Paste the new session string and click **Submit**.

The sensors come back within a minute, and the history you missed while logged
out is filled into the Energy dashboard automatically.

---

## Removing the account

**Settings → Devices & services → Singapore**, find the **SP Services**
account, **⋮ → Delete**. This removes its sensors and the stored session. The
statistics already imported stay in Home Assistant's history, so past Energy
dashboard data remains; remove the SP statistic from the Energy settings if
you no longer want it shown.

---

## Troubleshooting

**"uv is not recognized" (Windows) / "command not found: uv" (Mac)**
Close every PowerShell or Terminal window and open a new one. If that doesn't
help, restart your computer, or run the install line from Step 1 again.

**Windows: "running scripts is disabled on this system"**
Copy the install line exactly as shown. It includes `-ExecutionPolicy ByPass`,
which allows just this one install.

**No browser window opens / "no supported browser"**
Install [Google Chrome](https://www.google.com/chrome/), or use Microsoft
Edge. If your browser is installed in an unusual place, tell the program where
it is by running this in the same window first, then running the `uvx`
command again:

- Windows (PowerShell): `$env:SP_SERVICES_BROWSER = "C:\Path\To\chrome.exe"`
- Mac: `export SP_SERVICES_BROWSER="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"`

**The browser closed but no long line was printed**
Scroll up in PowerShell or Terminal and look for an error message. Run the
command again; if SP's login page was slow, the program may have given up
waiting.

**Home Assistant says "That is not a valid session string"**
Part of the line was cut off when copying. Copy the whole line again, from
the first to the last character, with no spaces or line breaks.

**Home Assistant says "This session has already ended"**
The session string was used too late, or SP ended it. Run the command again
and paste the new string straight away.

**"Could not reach SP Services"**
SP's site is down or slow. Try again in a few minutes.

**I can't find the SP sensor in the Energy dashboard's picker**
That's expected: the dashboard uses the imported statistics, not the sensors
(see [Step 4](#step-4--show-your-usage-in-the-energy-dashboard)). Pick
**SP Services \<account\> electricity** or **… water**. If they aren't listed,
check that **Import usage history into statistics** is ticked: use
**Reconfigure** on the account and wait up to 30 minutes for the next update.
The water statistic only has completed months, so it appears once SP has
published at least one full month.

**The Energy dashboard shows no electricity for today**
SP publishes readings with a delay (see [What to expect](#what-to-expect)),
so the most recent hours are often empty. Check again later, or look at
yesterday.

**I have more than one SP account (several homes)**
The integration currently uses the first account linked to your SP login.
If you need another one, please open an issue.

**Something else**
Use **⋮ → Download diagnostics** on the Singapore integration and attach the
file to a [GitHub issue](https://github.com/kianhean/singapore-homeassistant/issues).
The file never contains your session string or account number.

---

## Removing uv (optional)

You can keep `uv` to log in again later, or remove it:

- **Windows (PowerShell):**
  ```powershell
  uv cache clean
  Remove-Item "$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.local\bin\uvx.exe"
  ```
- **Mac (Terminal):**
  ```bash
  uv cache clean
  rm ~/.local/bin/uv ~/.local/bin/uvx
  ```

---

## Privacy

- The login happens in a temporary browser profile on your computer, which is
  deleted afterwards. Your SP password is never given to Home Assistant or to
  this integration.
- Home Assistant only talks to SP (`services.spservices.sg`,
  `identity.spdigital.auth0.com`, and SP's data API). Nothing is sent anywhere
  else.
- This uses SP's private web endpoints through the open-source
  [`sp_api`](https://github.com/kianhean/sp_api) library. It isn't an official
  SP Group product, and SP may change their site at any time.
