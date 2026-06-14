import { Link, useLocation } from "react-router-dom";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";

export function AppSidebar({ pages, logout, status }: { 
  pages: Array<{ id: string; path: string; label: string; description: string }>; 
  logout: () => void;
  status: string;
}) {
  const location = useLocation();

  return (
    <Sidebar variant="sidebar" collapsible="icon">
      <SidebarHeader className="p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="flex items-center justify-center w-8 h-8 rounded-md bg-primary text-primary-foreground font-bold">
            SR
          </div>
          <div className="flex flex-col group-data-[collapsible=icon]:hidden">
            <span className="font-semibold text-sm">SciFinder Route</span>
            <span className="text-xs text-muted-foreground">Admin Console</span>
          </div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Navigation</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {pages.map((item) => {
                const isActive = item.path === '/' 
                  ? location.pathname === '/' 
                  : location.pathname.startsWith(item.path);

                return (
                  <SidebarMenuItem key={item.id}>
                    <SidebarMenuButton 
                      isActive={isActive} 
                      tooltip={item.label}
                      render={
                        <Link to={item.path} className="flex flex-col items-start justify-center h-auto py-2">
                          <span className="text-sm font-medium leading-none">{item.label}</span>
                          <span className="text-xs text-muted-foreground group-data-[collapsible=icon]:hidden mt-1 line-clamp-1">{item.description}</span>
                        </Link>
                      }
                    />
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="p-4 border-t border-border flex-row justify-between items-center group-data-[collapsible=icon]:flex-col group-data-[collapsible=icon]:gap-2">
        <div className="px-2 py-1 bg-muted rounded text-xs uppercase font-medium tracking-wide">
          {status}
        </div>
        <button onClick={logout} className="text-xs text-muted-foreground hover:text-foreground group-data-[collapsible=icon]:hidden">
          退出
        </button>
      </SidebarFooter>
    </Sidebar>
  );
}
