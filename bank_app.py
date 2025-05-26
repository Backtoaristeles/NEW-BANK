import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta, datetime
import os

# ==== CONFIGURATION ====
DATA_DIR = "data"
TX_FILE = os.path.join(DATA_DIR, "transactions.csv")
NAV_FILE = os.path.join(DATA_DIR, "nav.csv")
AUDIT_FILE = os.path.join(DATA_DIR, "audit.csv")

# --- Use secrets for admin password ---
ADMIN_USER = st.secrets["admin"]["username"] if "admin" in st.secrets else "Admin"
ADMIN_PASS = st.secrets["admin"]["password"] if "admin" in st.secrets else "AdminPOEconomics"
START_DATE = date(2025, 5, 18)
DEFAULT_WITHDRAW_FEE = 0.03
DEFAULT_PROFIT_FEE = 0.02

os.makedirs(DATA_DIR, exist_ok=True)

# ==== HELPERS ====
def load_csv(f, columns):
    if os.path.exists(f):
        df = pd.read_csv(f)
        for col in ["Date", "Timestamp"]:
            if col in df.columns:
                df[col] = df[col].astype(str)
        return df
    else:
        return pd.DataFrame(columns=columns)

def save_csv(df, f):
    df.to_csv(f, index=False)

def append_audit(action, details, admin):
    audit = load_csv(AUDIT_FILE, ["Timestamp", "Action", "Details", "Admin"])
    new_row = pd.DataFrame([{
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Action": action,
        "Details": details,
        "Admin": admin,
    }])
    audit = pd.concat([audit, new_row], ignore_index=True)
    save_csv(audit, AUDIT_FILE)

def to_money(val):
    return f"{val:,.2f}".replace(",", " ")

# ==== LOAD STATE OR INIT ====
if "transactions" not in st.session_state:
    st.session_state["transactions"] = load_csv(TX_FILE, ["Date", "User", "Type", "Amount"])
if "nav" not in st.session_state:
    nav_df = load_csv(NAV_FILE, ["Date", "NAV"])
    st.session_state["nav"] = {row["Date"]: float(row["NAV"]) for _, row in nav_df.iterrows()}
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False
if "withdraw_fee" not in st.session_state:
    st.session_state["withdraw_fee"] = DEFAULT_WITHDRAW_FEE
if "profit_fee" not in st.session_state:
    st.session_state["profit_fee"] = DEFAULT_PROFIT_FEE

# ==== AUTH ====
def admin_login():
    with st.form("admin_login_form", clear_on_submit=True):
        st.write("🔑 **Admin Login**")
        user = st.text_input("Username")
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        if submitted:
            if user == ADMIN_USER and pw == ADMIN_PASS:
                st.session_state["is_admin"] = True
                st.success("Admin mode enabled.")
                append_audit("AdminLogin", "Logged in", user)
                st.rerun()
            else:
                st.error("Wrong username or password.")

def admin_logout():
    st.session_state["is_admin"] = False
    st.info("Logged out.")
    append_audit("AdminLogout", "Logged out", ADMIN_USER)
    st.rerun()

# ==== CALCULATION LOGIC ====
def recalculate_fund(transactions, nav_history, withdraw_fee, profit_fee):
    if "Amount" in transactions:
        transactions["Amount"] = pd.to_numeric(transactions["Amount"], errors="coerce").fillna(0.0)

    all_dates = sorted(set(nav_history.keys()) | set(transactions["Date"].astype(str).unique()))
    if not all_dates:
        return {}, {}, {}, {}, {}, pd.DataFrame(), {}
    nav_per_share = {}
    total_shares = 0
    share_ledger = []
    user_share_balances = {u: 0.0 for u in transactions["User"].unique() if u}
    for d in all_dates:
        d_str = str(d)
        nav = float(nav_history.get(d_str, np.nan))
        txs_today = transactions[transactions["Date"].astype(str) == d_str]
        nav_per_share_today = nav / total_shares if total_shares > 0 else 1.0
        for i, row in txs_today.iterrows():
            amt = float(row["Amount"])
            u = row["User"]
            ttype = row["Type"]
            if ttype == "Deposit":
                shares = amt / nav_per_share_today if nav_per_share_today > 0 else 0
                total_shares += shares
                user_share_balances[u] = user_share_balances.get(u, 0.0) + shares
            elif ttype == "Withdrawal":
                shares = amt / nav_per_share_today if nav_per_share_today > 0 else 0
                shares = min(shares, user_share_balances.get(u, 0.0))
                amt = shares * nav_per_share_today
                total_shares -= shares
                user_share_balances[u] = user_share_balances.get(u, 0.0) - shares
                amt = -amt
            else:
                continue
            share_ledger.append({
                "Date": d_str, "User": u, "Type": ttype, "Amount": amt,
                "Shares": shares, "NAV/Share": nav_per_share_today
            })
        nav_per_share[d_str] = nav / total_shares if total_shares > 0 else 1.0
    user_shares = {u: user_share_balances[u] for u in user_share_balances}
    current_nav_share = nav_per_share.get(all_dates[-1], 1.0)
    user_value = {u: user_shares[u] * current_nav_share for u in user_shares}
    deposit_sum = transactions[transactions["Type"]=="Deposit"].groupby("User")["Amount"].sum().to_dict()
    withdrawal_series = transactions[transactions["Type"]=="Withdrawal"].groupby("User")["Amount"].sum()
    withdrawal_sum = {u: -v for u, v in withdrawal_series.to_dict().items()}
    profit = {u: user_value[u] - deposit_sum.get(u, 0.0) + withdrawal_sum.get(u, 0.0) for u in user_shares}
    after_fees = {}
    fee_details = {}
    for u in user_shares:
        gross = user_value[u]
        withdrawal_fee_amt = gross * withdraw_fee
        profit_fee_amt = max(profit[u], 0) * profit_fee
        after_fees[u] = gross - withdrawal_fee_amt - profit_fee_amt
        fee_details[u] = {"withdrawal_fee": withdrawal_fee_amt, "profit_fee": profit_fee_amt}
    ledger_df = pd.DataFrame(share_ledger)
    return nav_per_share, user_shares, user_value, after_fees, profit, ledger_df, fee_details

# ==== MAIN UI ====
st.set_page_config("FundBank", layout="wide")
st.title("💎 FundBank — Fair Shares Tracking")
st.markdown(
    "<div style='color: #aaa;'>Admins can manage deposits/withdrawals/NAV. Users see wallets, graphs, and search history.<br>"
    "<b>Tip:</b> If your wallet is missing, contact an admin.</div>", unsafe_allow_html=True
)

nav_per_share, user_shares, user_value, after_fees, profit, ledger_df, fee_details = recalculate_fund(
    st.session_state["transactions"], st.session_state["nav"],
    st.session_state["withdraw_fee"], st.session_state["profit_fee"]
)

wallet_data = pd.DataFrame([
    {
        "User": u,
        "Shares": user_shares[u],
        "Wallet (Divines)": user_value[u],
        "After Fees": after_fees[u],
        "Profit": profit[u]
    } for u in user_value if u
])
if not wallet_data.empty and "Wallet (Divines)" in wallet_data.columns:
    wallet_data = wallet_data.sort_values("Wallet (Divines)", ascending=False)

# ========== USER MODE ==========
st.header("All Wallets (as of today)")
if wallet_data.empty:
    st.info("No deposits or NAV data entered yet.")
else:
    search_user = st.text_input("🔍 Search Wallet/User", "")
    filtered = wallet_data[wallet_data["User"].str.contains(search_user, case=False, na=False)] if search_user else wallet_data
    st.dataframe(filtered.style.format({
        "Shares": "{:.4f}",
        "Wallet (Divines)": "{:.2f}",
        "After Fees": "{:.2f}",
        "Profit": "{:.2f}",
    }), use_container_width=True)
    st.markdown(
        "<span style='font-size:12px;color:#888;'>"
        "Wallet values update daily based on fund NAV and may fluctuate with performance.<br>"
        "Withdrawals incur fees as configured by admins."
        "</span>", unsafe_allow_html=True
    )

st.subheader("📈 Fund NAV Over Time")
if st.session_state["nav"]:
    nav_hist = pd.DataFrame(sorted(st.session_state["nav"].items()), columns=["Date", "NAV"])
    nav_hist["Date"] = pd.to_datetime(nav_hist["Date"])
    st.line_chart(nav_hist.set_index("Date")["NAV"], height=220)
else:
    st.info("No NAV data yet for chart.")

st.subheader("📈 Individual Wallet Growth")
if not wallet_data.empty:
    wallet_users = list(wallet_data["User"])
    user_for_chart = st.selectbox("Select user for wallet history", wallet_users)
    ledger_df_user = ledger_df[ledger_df["User"] == user_for_chart]
    if not ledger_df_user.empty:
        wallet_chart = []
        running_shares = 0
        for idx, row in ledger_df_user.iterrows():
            if row["Type"] == "Deposit":
                running_shares += row["Shares"]
            elif row["Type"] == "Withdrawal":
                running_shares -= row["Shares"]
            nav = nav_per_share.get(row["Date"], 1.0)
            wallet_chart.append({"Date": row["Date"], "Wallet": running_shares * nav})
        wallet_chart = pd.DataFrame(wallet_chart)
        wallet_chart["Date"] = pd.to_datetime(wallet_chart["Date"])
        st.line_chart(wallet_chart.set_index("Date")["Wallet"], height=220)
    else:
        st.info("No transactions for this user.")
else:
    st.info("No wallet/user data for chart.")

st.markdown("---")

# ========== ADMIN ONLY ==========
if not st.session_state["is_admin"]:
    with st.expander("🔒 Admin Login", expanded=False):
        admin_login()
    st.stop()

# ========== ADMIN CONTROLS ==========
st.success("Admin mode enabled. All controls unlocked.")
st.caption("Admins can change all settings, fees, fund value, and transactions.")

# -- Fee Controls --
st.markdown("#### ⚙️ Fund Settings (Live Fees)")
c1, c2 = st.columns(2)
with c1:
    st.session_state["withdraw_fee"] = st.number_input(
        "Withdrawal Fee (%)",
        min_value=0.0, max_value=20.0,
        value=st.session_state["withdraw_fee"]*100, step=0.01, format="%.2f"
    )/100
with c2:
    st.session_state["profit_fee"] = st.number_input(
        "Profit Fee (%)",
        min_value=0.0, max_value=20.0,
        value=st.session_state["profit_fee"]*100, step=0.01, format="%.2f"
    )/100

# -- Deposit/Withdraw --
st.markdown("### New Deposit or Withdrawal")
with st.form("add_tx", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns([2,2,2,2])
    user = c1.text_input("User (Wallet)", "")
    ttype = c2.selectbox("Type", ["Deposit", "Withdrawal"])
    amt = c3.number_input("Amount (Divines)", min_value=0.01, step=0.01, value=10.0, format="%.2f")
    tx_date = c4.date_input("Date", value=date.today(), min_value=START_DATE)
    submit = st.form_submit_button("Add Entry")
    if submit and user:
        tx = pd.DataFrame([[str(tx_date), user.strip(), ttype, amt]], columns=["Date","User","Type","Amount"])
        st.session_state["transactions"] = pd.concat([st.session_state["transactions"], tx], ignore_index=True)
        save_csv(st.session_state["transactions"], TX_FILE)
        append_audit("AddTx", f"{ttype} {amt} for {user} on {tx_date}", ADMIN_USER)
        st.success(f"{ttype} for {user} added.")
        st.rerun()

# -- Danger Zone: Delete Complete Wallet --
st.markdown("---")
st.markdown("<div style='color:red;font-weight:bold;'>🗑️ Danger Zone: Delete Complete Wallet</div>", unsafe_allow_html=True)
st.warning(
    "This will permanently remove ALL transactions for the selected user. "
    "This action cannot be undone. Use with caution!"
)

# Get all users who currently have at least one transaction
existing_users = sorted(st.session_state["transactions"]["User"].dropna().unique())

if existing_users:
    del_user = st.selectbox(
        "Select wallet/user to delete completely", existing_users, key="delete_wallet_user"
    )

    # Step 2: Extra confirmation input
    confirm = st.text_input(
        f"Type the username '{del_user}' below to confirm deletion:",
        key="delete_wallet_confirm"
    )

    # Step 3: Deletion button (only enabled if confirm matches del_user)
    delete_disabled = (confirm != del_user)
    del_col1, del_col2 = st.columns([1, 5])
    with del_col1:
        if st.button("Delete Wallet", key="delete_wallet_btn", disabled=delete_disabled):
            before_count = len(st.session_state["transactions"])
            st.session_state["transactions"] = st.session_state["transactions"][st.session_state["transactions"]["User"] != del_user]
            save_csv(st.session_state["transactions"], TX_FILE)
            append_audit(
                "DeleteWallet",
                f"Deleted ALL transactions for user '{del_user}' ({before_count - len(st.session_state['transactions'])} removed)",
                ADMIN_USER,
            )
            st.success(f"All transactions for '{del_user}' have been permanently deleted.")
            st.rerun()
    with del_col2:
        st.info("Button enabled only when username is typed exactly.")

    if confirm and (confirm != del_user):
        st.error("Username does not match. Deletion not enabled.")
else:
    st.info("No wallets available for deletion.")

# -- NAV input --
st.markdown("### Edit Fund NAV per Day")
today = date.today()
min_date = START_DATE
days_range = (today - min_date).days + 1
nav_changed = False
for i in range(days_range):
    d = min_date + timedelta(days=i)
    d_str = str(d)
    nav_val = st.session_state["nav"].get(d_str, 0.0)
    nav_input = st.number_input(f"{d} NAV (Total Fund Value)", min_value=0.0, value=nav_val, step=0.01, format="%.2f", key=f"nav_{d_str}")
    if nav_input != nav_val:
        st.session_state["nav"][d_str] = nav_input
        nav_changed = True

if st.button("Save NAV"):
    nav_save_df = pd.DataFrame([{"Date": d, "NAV": v} for d,v in st.session_state["nav"].items()])
    save_csv(nav_save_df, NAV_FILE)
    append_audit("SaveNAV", f"NAVs saved.", ADMIN_USER)
    st.success("Fund NAVs saved!")

if st.button("Admin Logout"):
    admin_logout()

# -- Audit Log & Export --
st.markdown("### 📄 Audit Log (All Admin Actions)")
audit_df = load_csv(AUDIT_FILE, ["Timestamp", "Action", "Details", "Admin"])
if audit_df.empty:
    st.info("No admin actions logged yet.")
else:
    st.dataframe(audit_df.sort_values("Timestamp", ascending=False), use_container_width=True)
    st.download_button(
        label="Download Audit Log CSV",
        data=audit_df.to_csv(index=False).encode(),
        file_name="audit_log.csv",
        mime="text/csv",
    )

# -- Backup/export --
st.markdown("### 🗃️ Backup/Restore")
colb1, colb2, colb3 = st.columns(3)
with colb1:
    st.download_button(
        label="Download Transactions CSV",
        data=st.session_state["transactions"].to_csv(index=False).encode(),
        file_name="transactions_backup.csv",
        mime="text/csv",
    )
with colb2:
    nav_save_df = pd.DataFrame([{"Date": d, "NAV": v} for d,v in st.session_state["nav"].items()])
    st.download_button(
        label="Download NAV CSV",
        data=nav_save_df.to_csv(index=False).encode(),
        file_name="nav_backup.csv",
        mime="text/csv",
    )
with colb3:
    uploaded = st.file_uploader("Restore Transactions CSV", type=["csv"])
    if uploaded is not None:
        st.session_state["transactions"] = pd.read_csv(uploaded)
        save_csv(st.session_state["transactions"], TX_FILE)
        append_audit("RestoreTx", "Transactions restored from upload.", ADMIN_USER)
        st.success("Transactions restored! Reload the app.")

st.caption("All data is saved in the /data folder as CSV for backup & easy editing.")
