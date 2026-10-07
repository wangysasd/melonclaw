import { Button, Modal } from "antd";
import { useState, type ReactNode } from "react";
import { readStorage, writeStorage } from "../state/storage";
import "../styles/compliance.css";

const CONSENT_KEY = "melonclaw.access_notice.v1";

export function AccessNoticeGate({ children }: { children: ReactNode }) {
  const [accepted, setAccepted] = useState(() => readStorage(CONSENT_KEY) === "accepted");
  if (__MELONCLAW_IS_RMS_BRAND__ || accepted) return children;

  return (
    <Modal open title="⚠️ 重要访问提示" closable={false} maskClosable={false} keyboard={false}
      width={560} className="access-notice" aria-describedby="access-notice-description"
      footer={<Button type="primary" autoFocus onClick={() => {
        writeStorage(CONSENT_KEY, "accepted");
        setAccepted(true);
      }}>我已确认并进入</Button>}>
      <div id="access-notice-description">
        <p>本网站为纯个人内部学习站点，仅向指定少数好友开放，所有金融分析内容均为个人学习推演记录，<strong className="access-notice-risk">不构成任何投资建议</strong>，访问者基于本站内容做出的任何投资决策，<strong className="access-notice-risk">盈亏全部自行承担</strong>。</p>
        <p>您确认已阅读并完全理解以上风险提示，同意仅将本站内容用于个人学习用途，<strong className="access-notice-risk">不对外传播任何站点内容</strong>。</p>
      </div>
    </Modal>
  );
}

export function ComplianceStatement() {
  return (
    <footer className="compliance-statement" aria-labelledby="compliance-statement-title">
      <h2 id="compliance-statement-title">【本站合规声明】</h2>
      <p>本网站为纯个人学习交流站点，仅向站点所有者及预先约定的少数特定好友开放，不面向任何社会公众提供公开互联网服务，所有内容均为非公开的内部学习研究资料，不对外引流、不做公开传播。</p>
      <p>本站点内所有金融分析内容、数据推演结论、市场相关观点，仅为个人学习过程中的模拟研究记录，不构成任何投资建议、操作指导或交易邀约，所有内容均不代表任何持牌金融机构立场。访问者基于本站内容做出的任何投资决策，产生的全部盈亏由其自行承担，站点所有者不承担任何民事赔偿责任。本站所有内容未承诺保本保收益、未夸大过往收益、未隐瞒任何投资风险，所有金融相关信息仅供访问者个人参考，访问者需自行通过官方持牌金融渠道核实全部关键信息。</p>
      <p>本站内所有原创分析内容、对接外部接口生成的衍生内容，均为个人非商业用途的学习资料，仅授权指定访问者个人内部查阅，禁止任何形式的转载、复制、二次传播或用于商业用途。未经站点所有者书面许可，任何人不得将本站内容对外扩散、发布至公开互联网平台。</p>
      <p>本站已在腾讯云提交ICP备案申请，所有内容严格遵守国家互联网信息服务、金融信息服务相关法律法规，不从事任何经营性互联网活动，不传播任何违规金融信息，接受监管部门与访问者的监督。</p>
    </footer>
  );
}
