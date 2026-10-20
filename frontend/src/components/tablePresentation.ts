/** Shared pagination presentation; each table keeps its own paging state. */
export const tablePagination = {
  showSizeChanger: false,
  showTotal: (total: number) => `共 ${total} 条`,
};
